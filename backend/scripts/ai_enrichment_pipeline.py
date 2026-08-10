"""LLM enrichment pipeline for raw PTT/Dcard course review JSON.

Reads raw scraper outputs, asks an LLM to extract course-review signals, validates
the result with Pydantic, and writes a unified enriched JSON file.

Environment variables loaded from .env / backend/.env:
    OPENAI_API_KEY or API_KEY          for --provider openai
    GOOGLE_API_KEY or GEMINI_API_KEY   for --provider google

Examples:
    python backend/scripts/ai_enrichment_pipeline.py --dry-run --limit 3
    python backend/scripts/ai_enrichment_pipeline.py --inputs data/ptt_reviews.json data/dcard_reviews.json
    python backend/scripts/ai_enrichment_pipeline.py --provider google --model gemini-1.5-flash
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tqdm import tqdm

LOGGER = logging.getLogger("ai_enrichment_pipeline")

DEFAULT_INPUTS = ["data/ptt_reviews.json", "data/dcard_reviews.json"]
DEFAULT_OUTPUT = "data/unified_reviews_enriched.json"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GOOGLE_MODEL = "gemini-1.5-flash"
DEFAULT_MAX_INPUT_CHARS = 3200
DEFAULT_CONCURRENCY = 3


class ReviewEnrichment(BaseModel):
    sweetness: float = Field(..., ge=1, le=5)
    hardness: float = Field(..., ge=1, le=5)
    chillness: float = Field(..., ge=1, le=5)
    tags: list[str] = Field(..., max_length=5)
    ai_summary: str = Field(..., max_length=80)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, tags: list[str]) -> list[str]:
        normalized: list[str] = []
        for tag in tags:
            text = str(tag).strip()
            if text and text not in normalized:
                normalized.append(text[:20])
        return normalized[:5]


@dataclass(slots=True)
class RawReview:
    source: str
    source_id: str
    url: str | None
    title: str
    content: str
    metadata: dict[str, Any]
    raw: dict[str, Any]


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_environment() -> None:
    load_dotenv(project_root() / ".env", override=False)
    load_dotenv(backend_root() / ".env", override=False)


def clean_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate_text(text: str, max_chars: int) -> str:
    text = clean_text(text)
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    return f"{text[:head]}\n\n...[中間內容因 token 限制省略]...\n\n{text[-tail:]}"


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        LOGGER.warning("Input JSON does not exist, skipped: %s", path)
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {"reviews": payload}
    if not isinstance(payload, dict):
        raise ValueError(f"Input root must be object or array: {path}")
    return payload


def extract_reviews(payload: dict[str, Any], fallback_source: str) -> list[dict[str, Any]]:
    for key in ("reviews", "posts", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    LOGGER.warning("No review array found for source=%s", fallback_source)
    return []


def normalize_raw_review(item: dict[str, Any], fallback_source: str) -> RawReview | None:
    source = str(item.get("source") or fallback_source).strip() or fallback_source
    title = clean_text(item.get("title") or item.get("post_title") or "")
    content = clean_text(item.get("content") or item.get("comment") or item.get("excerpt") or "")
    if not title and not content:
        return None

    if source == "ptt":
        source_id = str(item.get("url") or f"ptt:{title}:{item.get('posted_at_raw', '')}")
    elif source == "dcard":
        source_id = str(item.get("post_id") or item.get("id") or item.get("url") or title)
    else:
        source_id = str(item.get("id") or item.get("url") or title)

    metadata = {
        "board": item.get("board"),
        "matched_keyword": item.get("matched_keyword"),
        "author": item.get("author"),
        "created_at": item.get("created_at") or item.get("posted_at_raw"),
        "like_count": item.get("like_count"),
        "comment_count": item.get("comment_count"),
        "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
    }
    return RawReview(
        source=source,
        source_id=source_id,
        url=item.get("url"),
        title=title,
        content=content,
        metadata=metadata,
        raw=item,
    )


def load_raw_reviews(input_paths: list[str]) -> list[RawReview]:
    reviews: list[RawReview] = []
    for input_path in input_paths:
        path = Path(input_path)
        fallback_source = "dcard" if "dcard" in path.name.lower() else "ptt" if "ptt" in path.name.lower() else "unknown"
        payload = read_json(path)
        for item in extract_reviews(payload, fallback_source):
            normalized = normalize_raw_review(item, fallback_source)
            if normalized:
                reviews.append(normalized)

    deduped: dict[str, RawReview] = {}
    for review in reviews:
        key = f"{review.source}:{review.source_id}"
        deduped.setdefault(key, review)
    return list(deduped.values())


def build_prompt(review: RawReview, max_input_chars: int) -> list[dict[str, str]]:
    body = truncate_text(
        f"標題：{review.title}\n來源：{review.source}\n看板：{review.metadata.get('board') or ''}\n內文：\n{review.content}",
        max_input_chars,
    )
    system = (
        "你是成大課程評價資料清洗器。請只根據使用者提供的文字判斷，不要自行補充事實。"
        "輸出必須是有效 JSON，且只能包含 sweetness, hardness, chillness, tags, ai_summary。"
        "分數定義：1=很低，5=很高。sweetness 是給分甜度；hardness 是課程硬度/負擔；"
        "chillness 是涼度/輕鬆程度。tags 最多 5 個短關鍵字。ai_summary 限 50 個中文字內。"
        "若資訊不足，請給 3.0 中性分數並在 tags 標示「資訊不足」。"
    )
    user = (
        "請把以下課程評價結構化為 JSON：\n\n"
        f"{body}\n\n"
        "JSON 格式範例："
        '{"sweetness":3.0,"hardness":3.0,"chillness":3.0,"tags":["資訊不足"],"ai_summary":"評價資訊有限。"}'
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


class LLMClient:
    def __init__(self, *, provider: Literal["openai", "google"], model: str, max_input_chars: int) -> None:
        self.provider = provider
        self.model = model
        self.max_input_chars = max_input_chars
        if provider == "openai":
            api_key = (os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY") or "").strip()
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY or API_KEY is missing.")
            from openai import AsyncOpenAI

            self.client = AsyncOpenAI(api_key=api_key)
        else:
            api_key = (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
            if not api_key:
                raise RuntimeError("GOOGLE_API_KEY or GEMINI_API_KEY is missing.")
            from langchain_google_genai import ChatGoogleGenerativeAI

            self.client = ChatGoogleGenerativeAI(
                model=model,
                google_api_key=api_key,
                temperature=0,
            )

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential_jitter(initial=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def enrich(self, review: RawReview) -> ReviewEnrichment:
        messages = build_prompt(review, self.max_input_chars)
        if self.provider == "openai":
            response = await self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                max_tokens=220,
                response_format={"type": "json_object"},
                messages=messages,
            )
            content = response.choices[0].message.content or "{}"
        else:
            result = await self.client.ainvoke(
                [
                    ("system", messages[0]["content"]),
                    ("human", messages[1]["content"]),
                ]
            )
            content = str(result.content or "{}")

        payload = parse_json_object(content)
        return ReviewEnrichment.model_validate(payload)


def fallback_enrichment(error: str) -> ReviewEnrichment:
    LOGGER.warning("Using neutral fallback enrichment: %s", error)
    return ReviewEnrichment(
        sweetness=3.0,
        hardness=3.0,
        chillness=3.0,
        tags=["AI清洗失敗"],
        ai_summary="AI 清洗失敗，需人工複查。",
    )


def merge_review(review: RawReview, enrichment: ReviewEnrichment, *, error: str | None = None) -> dict[str, Any]:
    merged = {
        "source": review.source,
        "source_id": review.source_id,
        "url": review.url,
        "title": review.title,
        "content": review.content,
        "metadata": review.metadata,
        "sweetness": enrichment.sweetness,
        "hardness": enrichment.hardness,
        "chillness": enrichment.chillness,
        "tags": enrichment.tags,
        "ai_summary": enrichment.ai_summary,
        "ai_enrichment_error": error,
    }
    return merged


async def enrich_all(
    reviews: list[RawReview],
    *,
    llm: LLMClient | None,
    concurrency: int,
    dry_run: bool,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(concurrency)
    results: list[dict[str, Any] | None] = [None] * len(reviews)

    async def worker(index: int, review: RawReview) -> None:
        async with semaphore:
            if dry_run:
                enrichment = ReviewEnrichment(
                    sweetness=3.0,
                    hardness=3.0,
                    chillness=3.0,
                    tags=["dry-run"],
                    ai_summary="Dry run，未呼叫 LLM。",
                )
                results[index] = merge_review(review, enrichment)
                return
            try:
                assert llm is not None
                enrichment = await llm.enrich(review)
                results[index] = merge_review(review, enrichment)
            except (ValidationError, Exception) as exc:
                error = str(exc)
                results[index] = merge_review(review, fallback_enrichment(error), error=error)

    tasks = [asyncio.create_task(worker(index, review)) for index, review in enumerate(reviews)]
    for task in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="AI enriching reviews"):
        await task
    return [item for item in results if item is not None]


def write_output(records: list[dict[str, Any]], output: str, *, args: argparse.Namespace) -> None:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "ai_enrichment_pipeline",
        "provider": args.provider,
        "model": args.model,
        "input_files": args.inputs,
        "review_count": len(records),
        "reviews": records,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LOGGER.info("Wrote enriched reviews to %s", output_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", default=DEFAULT_INPUTS)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--provider", choices=["openai", "google"], default="openai")
    parser.add_argument("--model", default="", help="Defaults to gpt-4o-mini or gemini-1.5-flash.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--max-input-chars", type=int, default=DEFAULT_MAX_INPUT_CHARS)
    parser.add_argument("--dry-run", action="store_true", help="Do not call LLM; write neutral sample output.")
    parser.add_argument("--verbose", action="store_true")
    return parser


async def async_main(args: argparse.Namespace) -> int:
    load_environment()
    args.model = args.model or (DEFAULT_OPENAI_MODEL if args.provider == "openai" else DEFAULT_GOOGLE_MODEL)
    raw_reviews = load_raw_reviews(args.inputs)
    if args.limit:
        raw_reviews = raw_reviews[: args.limit]

    if not raw_reviews:
        LOGGER.warning("No raw reviews found. Nothing to enrich.")
        write_output([], args.output, args=args)
        return 0

    llm = LLMClient(
        provider=args.provider,
        model=args.model,
        max_input_chars=args.max_input_chars,
    ) if not args.dry_run else None

    records = await enrich_all(
        raw_reviews,
        llm=llm,
        concurrency=max(args.concurrency, 1),
        dry_run=args.dry_run,
    )
    write_output(records, args.output, args=args)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user.")
        return 130
    except Exception as exc:
        LOGGER.error("AI enrichment failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
