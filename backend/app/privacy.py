from __future__ import annotations

import asyncio
import logging
import re
from difflib import SequenceMatcher
from os import getenv
from typing import Any


logger = logging.getLogger(__name__)
REDACTION = "[隱私屏蔽]"
STUDENT_NAME_REPLACEMENT = "某同學"
LLM_REVIEW_THRESHOLD = 200

# NCKU student numbers use one Latin program/department letter followed by
# eight digits. Tight alphanumeric boundaries avoid matching inside URLs,
# hashes, or longer identifiers.
NCKU_STUDENT_ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]\d{8}(?![A-Za-z0-9])"
)
TAIWAN_MOBILE_PATTERN = re.compile(
    r"(?<!\d)(?:09|\+886[\s-]?9)\d{2}(?:[\s-]?\d{3}){2}(?!\d)"
)
EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[\w.+-]+@(?:[\w-]+\.)+[A-Za-z]{2,63}(?![\w.-])",
    re.IGNORECASE,
)
TAIWAN_ID_CANDIDATE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z][12]\d{8}(?![A-Za-z0-9])"
)

TAIWAN_ID_LETTER_CODES = {
    letter: code
    for letter, code in zip(
        "ABCDEFGHJKLMNPQRSTUVXYWZIO",
        range(10, 36),
        strict=True,
    )
}

STUDENT_NAME_REVIEW_SYSTEM_PROMPT = """你是校園評論的隱私清洗器。

請檢查使用者提供的校園評論。只有在文字明確指向可識別的「學生、同學、學長姐、
作者本人」真實姓名時，才把該姓名替換為「某同學」，並輸出清洗後的完整原文。

嚴格規則：
1. 不得改寫、摘要、評論或補充內容，保持其餘語意與格式不變。
2. 不得替換教授、教師、研究員或職員姓名；帶有「教授、老師、博士、主任」等
   教職稱謂的人名必須保留。
3. 不得替換系所、課程、實驗室、校舍、道路或地點名稱，例如「光電系」、
   「理學大樓」。
4. 不確定是否為學生真實姓名時不要替換。
5. 只輸出清洗後全文，不要輸出說明、標題或 Markdown code fence。
"""


def is_valid_taiwan_national_id(candidate: str) -> bool:
    """Validate Taiwan national IDs to avoid masking ordinary serial codes."""

    normalized = candidate.upper()
    if not re.fullmatch(r"[A-Z][12]\d{8}", normalized):
        return False

    letter_code = TAIWAN_ID_LETTER_CODES.get(normalized[0])
    if letter_code is None:
        return False

    digits = [int(value) for value in normalized[1:]]
    checksum = (
        letter_code // 10
        + (letter_code % 10) * 9
        + sum(
            digit * weight
            for digit, weight in zip(
                digits[:8],
                range(8, 0, -1),
                strict=True,
            )
        )
        + digits[8]
    )
    return checksum % 10 == 0


def redact_direct_identifiers(text: str) -> str:
    """Mask deterministic identifiers without touching names or place terms."""

    sanitized = NCKU_STUDENT_ID_PATTERN.sub(REDACTION, text)
    sanitized = TAIWAN_MOBILE_PATTERN.sub(REDACTION, sanitized)
    sanitized = EMAIL_PATTERN.sub(REDACTION, sanitized)

    def replace_national_id(match: re.Match[str]) -> str:
        candidate = match.group(0)
        return REDACTION if is_valid_taiwan_national_id(candidate) else candidate

    return TAIWAN_ID_CANDIDATE_PATTERN.sub(
        replace_national_id,
        sanitized,
    )


def sanitize_nested_text(value: Any) -> Any:
    """Recursively redact strings in JSON-like metadata and tag collections."""

    if isinstance(value, str):
        return redact_direct_identifiers(value)
    if isinstance(value, list):
        return [sanitize_nested_text(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_nested_text(item) for item in value)
    if isinstance(value, dict):
        return {
            key: sanitize_nested_text(item)
            for key, item in value.items()
        }
    return value


def _llm_review_enabled() -> bool:
    return getenv("PII_LLM_REVIEW_ENABLED", "false").lower() == "true"


def _clean_llm_output(output: str) -> str:
    cleaned = output.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3:
            cleaned = "\n".join(lines[1:-1]).strip()
    return cleaned


async def _review_student_names_with_openai(text: str) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=getenv("PII_OPENAI_API_KEY") or getenv("OPENAI_API_KEY"),
    )
    response = await client.chat.completions.create(
        model=getenv("PII_OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": STUDENT_NAME_REVIEW_SYSTEM_PROMPT,
            },
            {"role": "user", "content": text},
        ],
    )
    return response.choices[0].message.content or ""


async def _review_student_names_with_google(text: str) -> str:
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import HumanMessage, SystemMessage

    model = ChatGoogleGenerativeAI(
        model=getenv("PII_GOOGLE_MODEL", "gemini-1.5-flash"),
        google_api_key=getenv("GOOGLE_API_KEY"),
        temperature=0,
    )
    response = await model.ainvoke(
        [
            SystemMessage(content=STUDENT_NAME_REVIEW_SYSTEM_PROMPT),
            HumanMessage(content=text),
        ]
    )
    return str(response.content)


async def anonymize_review_text(text: str) -> str:
    """
    Redact direct identifiers and optionally review long text for student names.

    The deterministic pass always runs before and after the optional provider.
    Provider failures never restore the original text and never log its content.
    """

    sanitized = redact_direct_identifiers(text)
    if (
        len(sanitized) <= LLM_REVIEW_THRESHOLD
        or not _llm_review_enabled()
    ):
        return sanitized

    provider = getenv("PII_LLM_PROVIDER", "openai").lower()
    timeout_seconds = float(getenv("PII_LLM_TIMEOUT_SECONDS", "6"))

    try:
        async with asyncio.timeout(timeout_seconds):
            if provider == "google":
                reviewed = await _review_student_names_with_google(sanitized)
            else:
                reviewed = await _review_student_names_with_openai(sanitized)
    except Exception as exc:
        logger.warning(
            "privacy_llm_review_failed",
            extra={
                "provider": provider,
                "text_length": len(sanitized),
                "exception_type": type(exc).__name__,
            },
        )
        return sanitized

    cleaned = _clean_llm_output(reviewed)
    output_is_invalid = (
        not cleaned
        or len(cleaned) > max(len(sanitized) * 2, 8000)
        or cleaned.count(REDACTION) < sanitized.count(REDACTION)
        or SequenceMatcher(None, sanitized, cleaned).ratio() < 0.75
    )
    if output_is_invalid:
        logger.warning(
            "privacy_llm_review_invalid_output",
            extra={
                "provider": provider,
                "input_length": len(sanitized),
                "output_length": len(cleaned),
            },
        )
        return sanitized

    return redact_direct_identifiers(cleaned)
