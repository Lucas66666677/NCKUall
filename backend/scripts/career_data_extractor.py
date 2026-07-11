"""
Career-planning extraction and structuring pipeline.

Features:
1. Extract text from exchange/pre-master/lab PDFs with PyMuPDF.
2. Split long text with LangChain RecursiveCharacterTextSplitter.
3. Infer metadata such as department and career category from text.
4. Crawl department faculty pages. The default parser targets NCKU DPS
   (Department of Photonics) faculty-list structure.
5. Upsert chunks and metadata into PostgreSQL/Supabase pgvector tables.

Local PDF test:
    cd backend
    pip install -r requirements.txt
    set DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/nckuall
    python scripts/career_data_extractor.py ^
      --pdf .\\samples\\exchange.pdf ^
      --source-title "海外交換簡章" ^
      --create-tables

Faculty crawl test:
    python scripts/career_data_extractor.py ^
      --faculty-url "https://dps.ncku.edu.tw/p/412-1174-23177.php?Lang=zh-tw" ^
      --department-code DPS ^
      --department-name "光電科學與工程學系" ^
      --create-tables

Dry run without DB writes:
    python scripts/career_data_extractor.py --faculty-url "..." --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re
import sys
from dataclasses import asdict, dataclass
from os import getenv
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import fitz
import httpx
from bs4 import BeautifulSoup
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models import Base, CareerDocumentChunk, Department, EMBEDDING_DIMENSIONS


DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/nckuall"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

DEPARTMENT_KEYWORDS = {
    "DPS": ["光電", "光電科學", "光電科學與工程", "photonics", "dps"],
    "EE": ["電機", "電機工程", "electrical engineering", "ee"],
    "CSIE": ["資工", "資訊工程", "computer science", "csie"],
    "ME": ["機械", "機械工程", "mechanical engineering"],
    "GLOBAL": ["全校", "全校通用", "所有系所", "不限科系", "校級"],
}

CATEGORY_KEYWORDS = {
    "overseas_exchange": ["交換", "海外", "姊妹校", "exchange", "abroad", "出國"],
    "lab_project": ["實驗室", "專題", "研究領域", "教授", "lab", "research"],
    "pre_master": ["預研", "五年一貫", "逕讀", "pre-master"],
    "grad_school": ["推甄", "研究所", "碩士班", "甄試", "graduate"],
    "transfer_department": ["轉系", "轉入", "轉出"],
    "program": ["計畫", "學程", "program", "scholarship", "獎學金"],
}


@dataclass(frozen=True)
class ExtractedChunk:
    content: str
    source_type: str
    source_url: str | None
    source_title: str | None
    chunk_index: int
    department_code: str
    category: str
    metadata: dict[str, Any]
    embedding: list[float]


@dataclass(frozen=True)
class ProfessorProfile:
    name_zh: str
    name_en: str | None
    title: str | None
    extension: str | None
    office: str | None
    email: str | None
    lab: str | None
    research_areas: str | None
    profile_urls: list[str]


class DepartmentSiteParser(Protocol):
    def parse_faculty(self, html: str, *, source_url: str) -> list[ProfessorProfile]:
        """Parse professor profiles from a department website."""


class DPSFacultyParser:
    """
    Parser for NCKU Department of Photonics faculty pages.

    The DPS page is a repeated label-list:
    professor name -> 分機 -> 辦公室 -> E-mail -> 實驗室 -> 專長領域.
    This parser uses that label sequence instead of fragile CSS classes.
    """

    NAME_PATTERN = re.compile(r"^[\u4e00-\u9fff]{2,4}$")
    TITLE_PATTERN = re.compile(
        r"^[（(](?P<title>[^）)]*(?:教授|系主任|講師|助理|副教授|特聘)[^）)]*)"
        r"[）)]\s*(?P<name_en>[A-Za-z][A-Za-z,\-. ]+)?$"
    )

    def parse_faculty(self, html: str, *, source_url: str) -> list[ProfessorProfile]:
        soup = BeautifulSoup(html, "html.parser")
        lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
        profiles: list[ProfessorProfile] = []

        index = 0
        while index < len(lines):
            title_match = self.TITLE_PATTERN.match(lines[index])
            if not title_match:
                index += 1
                continue

            block = lines[index : index + 18]
            name_zh = self._nearest_name_before(lines, index)
            if not name_zh:
                index += 1
                continue

            profiles.append(
                ProfessorProfile(
                    name_zh=name_zh,
                    title=title_match.group("title"),
                    name_en=(title_match.group("name_en") or "").strip() or None,
                    extension=self._value_after_label(block, "分機："),
                    office=self._value_after_label(block, "辦公室："),
                    email=self._value_after_label(block, "E-mail："),
                    lab=self._value_after_label(block, "實驗室："),
                    research_areas=self._value_after_label(block, "專長領域："),
                    profile_urls=[],
                )
            )
            index += 1

        return profiles

    def _nearest_name_before(self, lines: list[str], index: int) -> str | None:
        for previous in reversed(lines[max(0, index - 4) : index]):
            if self.NAME_PATTERN.match(previous) and previous not in {"首頁", "專任教師"}:
                return previous
        return None

    @staticmethod
    def _value_after_label(block: list[str], label: str) -> str | None:
        try:
            value = block[block.index(label) + 1]
        except (ValueError, IndexError):
            return None
        return normalize_text(value)

class PoliteCrawler:
    def __init__(self, *, timeout: float = 20.0, min_delay: float = 1.5) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=True)
        self._min_delay = min_delay
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def close(self) -> None:
        await self._client.aclose()

    async def get_text(self, url: str) -> str:
        async with self._lock:
            loop = asyncio.get_running_loop()
            elapsed = loop.time() - self._last_request_at
            if elapsed < self._min_delay:
                await asyncio.sleep(self._min_delay - elapsed + random.uniform(0, 0.8))
            self._last_request_at = loop.time()

        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
        }
        response = await self._client.get(url, headers=headers)
        response.raise_for_status()
        return response.text


def normalize_text(value: str | None) -> str | None:
    if not value:
        return None
    text_value = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    text_value = re.sub(r"[\u200b-\u200f\ufeff]", "", text_value)
    text_value = re.sub(r"\s+", " ", text_value)
    return text_value.strip() or None


def extract_pdf_text(pdf_path: Path) -> str:
    """Extract plain text from a PDF with PyMuPDF."""

    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    parts: list[str] = []
    with fitz.open(pdf_path) as document:
        for page_index, page in enumerate(document, start=1):
            text_value = normalize_text(page.get_text("text"))
            if text_value:
                parts.append(f"[page {page_index}] {text_value}")

    return "\n\n".join(parts)


def split_text(text_value: str, *, chunk_size: int, chunk_overlap: int) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "；", ";", "，", ",", " ", ""],
    )
    return splitter.split_text(text_value)


def infer_department_code(text_value: str, default_department_code: str | None = None) -> str:
    lowered = text_value.lower()
    scores: dict[str, int] = {}
    for code, keywords in DEPARTMENT_KEYWORDS.items():
        scores[code] = sum(1 for keyword in keywords if keyword.lower() in lowered)

    best_code, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score > 0:
        return best_code
    return default_department_code or "GLOBAL"


def infer_category(text_value: str, default_category: str | None = None) -> str:
    lowered = text_value.lower()
    scores: dict[str, int] = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        scores[category] = sum(1 for keyword in keywords if keyword.lower() in lowered)

    best_category, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score > 0:
        return best_category
    return default_category or "program"


def make_hash_embedding(text_value: str, *, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    """
    Deterministic placeholder embedding for local tests.

    Replace this function with OpenAI/Supabase Edge Function embeddings in
    production. Keeping dimensions at 1536 makes it compatible with the
    existing pgvector schema.
    """

    vector = [0.0] * dimensions
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", text_value.lower())
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        vector[index] += 1.0

    norm = sum(value * value for value in vector) ** 0.5
    if norm:
        vector = [round(value / norm, 6) for value in vector]
    return vector


def chunks_from_pdf(
    *,
    pdf_path: Path,
    source_title: str | None,
    source_url: str | None,
    default_department_code: str | None,
    default_category: str | None,
    chunk_size: int,
    chunk_overlap: int,
) -> list[ExtractedChunk]:
    text_value = extract_pdf_text(pdf_path)
    chunks = split_text(text_value, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    extracted: list[ExtractedChunk] = []

    for index, chunk in enumerate(chunks):
        department_code = infer_department_code(chunk, default_department_code)
        category = infer_category(chunk, default_category)
        extracted.append(
            ExtractedChunk(
                content=chunk,
                source_type="pdf",
                source_url=source_url or str(pdf_path),
                source_title=source_title or pdf_path.name,
                chunk_index=index,
                department_code=department_code,
                category=category,
                metadata={
                    "file_name": pdf_path.name,
                    "department_code": department_code,
                    "category": category,
                    "extraction": "pymupdf",
                },
                embedding=make_hash_embedding(chunk),
            )
        )

    return extracted


def chunks_from_professors(
    *,
    profiles: list[ProfessorProfile],
    source_url: str,
    department_code: str,
    department_name: str,
) -> list[ExtractedChunk]:
    extracted: list[ExtractedChunk] = []

    for index, profile in enumerate(profiles):
        content = normalize_text(
            f"{profile.name_zh} {profile.name_en or ''} {profile.title or ''}\n"
            f"實驗室：{profile.lab or ''}\n"
            f"專長領域：{profile.research_areas or ''}\n"
            f"辦公室：{profile.office or ''}\n"
            f"E-mail：{profile.email or ''}"
        )
        if not content:
            continue

        extracted.append(
            ExtractedChunk(
                content=content,
                source_type="department_faculty_html",
                source_url=source_url,
                source_title=f"{department_name}教授與研究領域",
                chunk_index=index,
                department_code=department_code,
                category="lab_project",
                metadata={
                    "department_code": department_code,
                    "department_name": department_name,
                    "professor": asdict(profile),
                    "category": "lab_project",
                    "parser": "DPSFacultyParser",
                },
                embedding=make_hash_embedding(content),
            )
        )

    return extracted


async def upsert_department(session: AsyncSession, *, code: str, name_zh: str) -> UUID | None:
    if code == "GLOBAL":
        return None

    stmt = (
        insert(Department)
        .values(code=code, name_zh=name_zh, is_active=True)
        .on_conflict_do_update(
            index_elements=[Department.code],
            set_={"name_zh": name_zh, "is_active": True},
        )
        .returning(Department.id)
    )
    return await session.scalar(stmt)


async def find_department_id(session: AsyncSession, code: str) -> UUID | None:
    if code == "GLOBAL":
        return None
    return await session.scalar(select(Department.id).where(Department.code == code))


async def write_chunks(
    session: AsyncSession,
    chunks: list[ExtractedChunk],
    *,
    department_names: dict[str, str],
) -> int:
    written = 0

    for chunk in chunks:
        department_name = department_names.get(chunk.department_code, chunk.department_code)
        department_id = await upsert_department(session, code=chunk.department_code, name_zh=department_name)
        if department_id is None:
            department_id = await find_department_id(session, chunk.department_code)

        metadata = {
            **chunk.metadata,
            "source_type": chunk.source_type,
            "source_url": chunk.source_url,
            "source_title": chunk.source_title,
        }
        source_key = f"{chunk.source_type}:{chunk.source_url}:{chunk.chunk_index}"

        values = {
            "department_id": department_id,
            "source_type": chunk.source_type,
            "source_url": chunk.source_url,
            "source_title": chunk.source_title,
            "category": chunk.category,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "metadata_json": {**metadata, "source_key": source_key},
            "embedding": chunk.embedding,
        }
        insert_stmt = (
            insert(CareerDocumentChunk)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_career_chunk_source_index",
                set_=values,
            )
        )
        await session.execute(insert_stmt)
        written += 1

    return written


async def run(args: argparse.Namespace) -> None:
    all_chunks: list[ExtractedChunk] = []
    department_names = {
        "DPS": args.department_name or "光電科學與工程學系",
        "EE": "電機工程學系",
        "CSIE": "資訊工程學系",
    }

    if args.pdf:
        for pdf in args.pdf:
            all_chunks.extend(
                chunks_from_pdf(
                    pdf_path=Path(pdf),
                    source_title=args.source_title,
                    source_url=args.source_url,
                    default_department_code=args.department_code,
                    default_category=args.category,
                    chunk_size=args.chunk_size,
                    chunk_overlap=args.chunk_overlap,
                )
            )

    crawler = PoliteCrawler(timeout=args.timeout, min_delay=args.min_delay)
    try:
        if args.faculty_url:
            html = await crawler.get_text(args.faculty_url)
            parser: DepartmentSiteParser = DPSFacultyParser()
            profiles = parser.parse_faculty(html, source_url=args.faculty_url)
            all_chunks.extend(
                chunks_from_professors(
                    profiles=profiles,
                    source_url=args.faculty_url,
                    department_code=args.department_code or "DPS",
                    department_name=args.department_name or "光電科學與工程學系",
                )
            )
    finally:
        await crawler.close()

    if args.output_json:
        Path(args.output_json).write_text(
            json.dumps([asdict(chunk) for chunk in all_chunks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if args.dry_run:
        preview = []
        for chunk in all_chunks[: args.preview_limit]:
            item = asdict(chunk)
            item["embedding"] = f"<vector:{len(chunk.embedding)} dims>"
            preview.append(item)
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        print(f"Dry run complete: chunks={len(all_chunks)}")
        return

    database_url = args.database_url or getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    engine = create_async_engine(database_url, pool_pre_ping=True)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    try:
        if args.create_tables:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await conn.run_sync(Base.metadata.create_all)

        async with async_session() as session:
            async with session.begin():
                written = await write_chunks(session, all_chunks, department_names=department_names)
        print(f"Career extraction complete: chunks_written={written}")
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract career planning data into pgvector chunks.")
    parser.add_argument("--pdf", action="append", help="PDF path. Can be used multiple times.")
    parser.add_argument("--faculty-url", help="Department faculty page URL.")
    parser.add_argument("--department-code", default="DPS", help="Default department code, e.g. DPS, EE, CSIE.")
    parser.add_argument("--department-name", default="光電科學與工程學系", help="Default department name.")
    parser.add_argument("--category", help="Default category for ambiguous PDF chunks.")
    parser.add_argument("--source-title", help="Human-readable PDF/source title.")
    parser.add_argument("--source-url", help="Original URL for a downloaded PDF.")
    parser.add_argument("--output-json", help="Write extracted chunks to JSON for inspection.")
    parser.add_argument("--database-url", help="PostgreSQL/Supabase URL. Defaults to DATABASE_URL env var.")
    parser.add_argument("--create-tables", action="store_true", help="Create pgvector extension and tables.")
    parser.add_argument("--dry-run", action="store_true", help="Print chunks without writing to DB.")
    parser.add_argument("--preview-limit", type=int, default=5, help="Number of chunks printed during dry run.")
    parser.add_argument("--chunk-size", type=int, default=900)
    parser.add_argument("--chunk-overlap", type=int, default=120)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--min-delay", type=float, default=1.5)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.pdf and not args.faculty_url:
        raise SystemExit("Provide at least one --pdf or --faculty-url.")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
