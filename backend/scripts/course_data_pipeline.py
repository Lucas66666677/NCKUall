"""
Async course-planning data pipeline for NCKU Hub.

What this script does:
1. Fetch course basic information from either mock fixtures or a future NCKU
   course endpoint.
2. Fetch historical grade distribution data from either mock fixtures or a
   future public/forum endpoint.
3. Clean syllabus HTML/text so it can later be embedded for RAG.
4. Upsert Department, Course, and CourseGradeDistribution records with
   SQLAlchemy async sessions.

Quick local test:
    cd backend
    pip install -r requirements.txt
    set DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/nckuall
    python scripts/course_data_pipeline.py --mock --create-tables

Future real crawling shape:
    python scripts/course_data_pipeline.py ^
      --course-url "https://course.ncku.edu.tw/index.php?c=qry11215" ^
      --grade-url "https://example.ncku.edu.tw/grades/history" ^
      --academic-year 114 ^
      --semester 1

Single-department parser validation without database writes:
    python scripts/course_data_pipeline.py ^
      --course-url "https://course.ncku.edu.tw/index.php?c=qry11215" ^
      --department-code DPS ^
      --validate-only

The public NCKU course page is an iframe shell. The pipeline follows the
course-query.acad.ncku.edu.tw iframe automatically, then parses Bootstrap/RWD
tables and common labeled cells with defensive fallbacks.

Be respectful when changing the real URLs:
- Check the target site's terms and robots policy.
- Keep concurrency low and delay high.
- Cache source pages where possible.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal
from os import getenv
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from uuid import UUID

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models import Base, Course, CourseDifficulty, CourseGradeDistribution, Department


DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/nckuall"
logger = logging.getLogger("course_data_pipeline")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
]

MOCK_COURSES_HTML = """
<html>
  <body>
    <table id="courses">
      <tr>
        <th>code</th><th>department_code</th><th>department_name</th>
        <th>title</th><th>teacher</th><th>credits</th><th>syllabus</th>
      </tr>
      <tr>
        <td>CSIE1001</td>
        <td>CSIE</td>
        <td>資訊工程學系</td>
        <td>資料結構</td>
        <td>王小明</td>
        <td>3</td>
        <td>
          <div>
            課程目標：理解陣列、鏈結串列、樹、圖與雜湊。
            <br />評量方式：作業 40%、期中 30%、期末 30%。
          </div>
        </td>
      </tr>
      <tr>
        <td>CSIE2002</td>
        <td>CSIE</td>
        <td>資訊工程學系</td>
        <td>演算法</td>
        <td>陳美玲</td>
        <td>3</td>
        <td>
          <p>介紹排序、搜尋、動態規劃、圖論與 NP 完全性。</p>
          <p>需要熟悉基本資料結構。</p>
        </td>
      </tr>
    </table>
  </body>
</html>
"""

MOCK_GRADES_JSON = {
    "items": [
        {
            "course_code": "CSIE1001",
            "department_code": "CSIE",
            "academic_year": 112,
            "semester": 1,
            "enrollment_count": 84,
            "a_plus_ratio": 0.18,
            "fail_ratio": 0.04,
            "source_url": "mock://grades/csie1001/112-1",
        },
        {
            "course_code": "CSIE1001",
            "department_code": "CSIE",
            "academic_year": 113,
            "semester": 1,
            "enrollment_count": 91,
            "a_plus_ratio": 0.21,
            "fail_ratio": 0.03,
            "source_url": "mock://grades/csie1001/113-1",
        },
        {
            "course_code": "CSIE2002",
            "department_code": "CSIE",
            "academic_year": 113,
            "semester": 2,
            "enrollment_count": 76,
            "a_plus_ratio": 0.12,
            "fail_ratio": 0.08,
            "source_url": "mock://grades/csie2002/113-2",
        },
    ]
}


@dataclass(frozen=True)
class CoursePayload:
    course_code: str
    department_code: str
    department_name: str
    title_zh: str
    instructor_name: str | None
    credits: Decimal | None
    required_for_major: bool
    time_room: str | None
    tags: list[str]
    description: str | None
    syllabus_url: str | None = None
    academic_year: int | None = None
    semester: int | None = None


@dataclass(frozen=True)
class GradePayload:
    course_code: str
    department_code: str
    academic_year: int
    semester: int
    enrollment_count: int | None
    a_plus_ratio: Decimal | None
    fail_ratio: Decimal | None
    source_url: str | None = None


class FetchSkippedError(RuntimeError):
    """Raised when a source page keeps refusing service after retries."""


class AsyncRateLimiter:
    """Small async rate limiter with jitter for polite crawling."""

    def __init__(self, *, min_interval_seconds: float, jitter_seconds: float) -> None:
        self.min_interval_seconds = min_interval_seconds
        self.jitter_seconds = jitter_seconds
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_at
            delay = self.min_interval_seconds - elapsed
            if delay > 0:
                await asyncio.sleep(delay + random.uniform(0, self.jitter_seconds))
            self._last_request_at = time.monotonic()


class PoliteHttpClient:
    """httpx wrapper that applies rate limiting, random UA, and retries."""

    def __init__(
        self,
        *,
        concurrency: int,
        min_interval_seconds: float,
        jitter_seconds: float,
        timeout_seconds: float,
    ) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._limiter = AsyncRateLimiter(
            min_interval_seconds=min_interval_seconds,
            jitter_seconds=jitter_seconds,
        )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_text(self, url: str, *, params: dict[str, Any] | None = None) -> str:
        async with self._semaphore:
            await self._limiter.wait()
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
            }

            last_error: Exception | None = None
            for attempt in range(1, 4):
                try:
                    response = await self._client.get(url, params=params, headers=headers)
                    if response.status_code in {403, 503}:
                        raise httpx.HTTPStatusError(
                            f"temporary refusal status={response.status_code}",
                            request=response.request,
                            response=response,
                        )
                    response.raise_for_status()
                    return response.text
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    status_code = (
                        exc.response.status_code
                        if exc.response is not None
                        else "unknown"
                    )
                    delay = (2 ** attempt) + random.uniform(0, 1.5)
                    logger.warning(
                        "HTTP status %s from %s attempt=%s/3; retrying in %.1fs",
                        status_code,
                        url,
                        attempt,
                        delay,
                    )
                    if attempt == 3:
                        raise FetchSkippedError(
                            f"skip source after repeated HTTP {status_code}: {url}"
                        ) from exc
                    await asyncio.sleep(delay)
                except httpx.HTTPError as exc:
                    last_error = exc
                    delay = (2 ** attempt) + random.uniform(0, 1.5)
                    logger.warning(
                        "HTTP error from %s attempt=%s/3; retrying in %.1fs: %s",
                        url,
                        attempt,
                        delay,
                        exc,
                    )
                    if attempt == 3:
                        raise FetchSkippedError(
                            f"skip source after repeated HTTP errors: {url}"
                        ) from exc
                    await asyncio.sleep(delay)

            raise FetchSkippedError(f"skip source after retries: {url}") from last_error


def clean_text(raw_html_or_text: str | None) -> str | None:
    """
    Clean syllabus text for future embedding.

    The function accepts either raw HTML or plain text, strips tags, normalizes
    whitespace, and removes invisible control characters.
    """

    if not raw_html_or_text:
        return None

    normalized_source = unicodedata.normalize("NFKC", str(raw_html_or_text))
    normalized_source = normalized_source.replace("\r\n", "\n").replace("\r", "\n")
    soup = BeautifulSoup(normalized_source, "html.parser")
    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\xa0", " ").replace("\u3000", " ")
    text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def parse_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    return Decimal(match.group(0))


def safe_text(node: Any, default: str = "") -> str:
    try:
        if node is None:
            return default
        return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() or default
    except Exception:
        return default


def normalize_header(value: str) -> str:
    value = re.sub(r"\s+", "", value).strip().lower()
    aliases = {
        "系所": "department",
        "開課系所": "department",
        "開課單位": "department",
        "dept": "department",
        "department": "department",
        "department_name": "department",
        "departmentname": "department",
        "系號": "department_code",
        "系所代碼": "department_code",
        "department_code": "department_code",
        "deptcode": "department_code",
        "code": "course_code",
        "課號": "course_code",
        "課程碼": "course_code",
        "課程代碼": "course_code",
        "科目代碼": "course_code",
        "courseid": "course_code",
        "coursecode": "course_code",
        "課程名稱": "title",
        "科目名稱": "title",
        "課名": "title",
        "coursename": "title",
        "任課教師": "instructor",
        "授課教師": "instructor",
        "教師": "instructor",
        "teacher": "instructor",
        "instructor": "instructor",
        "學分": "credits",
        "credit": "credits",
        "credits": "credits",
        "選必修": "required",
        "必選修": "required",
        "修別": "required",
        "類別": "required",
        "必/選修": "required",
        "時間教室": "time_room",
        "時間/教室": "time_room",
        "上課時間": "time_room",
        "上課教室": "time_room",
        "教室": "time_room",
        "時間": "time_room",
        "time": "time_room",
        "classroom": "time_room",
        "room": "time_room",
        "大綱": "syllabus",
        "課程大綱": "syllabus",
        "教學大綱": "syllabus",
        "課程目標": "syllabus",
        "courseoutline": "syllabus",
        "syllabus": "syllabus",
    }
    return aliases.get(value, value)


COURSE_FIELD_KEYS = {
    "department",
    "department_code",
    "course_code",
    "title",
    "instructor",
    "credits",
    "required",
    "time_room",
    "syllabus",
}


def safe_field(values: dict[str, str], key: str, default: str = "") -> str:
    try:
        value = values.get(key, default)
        if value is None:
            return default
        return re.sub(r"\s+", " ", str(value)).strip() or default
    except Exception:
        return default


def infer_department_code(raw_department: str, course_code: str) -> str:
    text_value = raw_department.strip()
    match = re.search(r"\b([A-Z]{2,8})\b", text_value.upper())
    if match:
        return match.group(1)
    match = re.match(r"([A-Z]{2,8})", course_code.upper())
    if match:
        return match.group(1)
    return "NCKU"


def infer_department_name(raw_department: str, department_code: str) -> str:
    text_value = raw_department.strip()
    text_value = re.sub(r"\b[A-Z]{2,8}\b", "", text_value).strip(" -_/　")
    return text_value or department_code


def infer_required(value: str) -> bool:
    normalized = value.strip()
    return "必" in normalized and "選" not in normalized


CORE_KEYWORD_RULES: dict[str, tuple[str, ...]] = {
    "AI": ("人工智慧", "機器學習", "深度學習", "資料探勘", "神經網路", "AI", "machine learning"),
    "半導體": ("半導體", "積體電路", "晶片", "製程", "IC", "VLSI"),
    "光電": ("光電", "雷射", "光學", "顯示", "photonics", "optics"),
    "程式設計": ("程式", "Python", "C++", "Java", "演算法", "資料結構"),
    "數學基礎": ("微積分", "線性代數", "機率", "統計", "工程數學"),
    "實作專題": ("實驗", "專題", "project", "lab", "實作"),
}


def extract_course_tags(*texts: str | None, required_for_major: bool) -> list[str]:
    joined = " ".join(text for text in texts if text)
    tags: list[str] = ["必修" if required_for_major else "選修"]
    for tag, keywords in CORE_KEYWORD_RULES.items():
        if any(keyword.lower() in joined.lower() for keyword in keywords):
            tags.append(tag)
    return list(dict.fromkeys(tags))


def maybe_course_row(text_value: str) -> bool:
    return bool(re.search(r"[A-Z]{2,8}\s*\d{3,5}|[A-Z]\d{4,}", text_value.upper())) or "課號" in text_value


class NckuCourseHtmlParser:
    """Robust parser for NCKU course-query HTML and compatible table exports."""

    course_row_selectors = (
        "table#courses tr",
        "table.rwd_table tr",
        "table.table tr",
        "table tr",
        ".rwd_tr",
        ".course-row",
        "[data-course-code]",
    )

    def __init__(
        self,
        *,
        academic_year: int | None,
        semester: int | None,
        base_url: str | None = None,
    ) -> None:
        self.academic_year = academic_year
        self.semester = semester
        self.base_url = base_url

    def parse(self, html: str) -> list[CoursePayload]:
        soup = BeautifulSoup(html, "html.parser")
        courses: list[CoursePayload] = []
        seen: set[tuple[str, str]] = set()

        for table in soup.select("table"):
            courses.extend(self._parse_table(table, seen))

        if not courses:
            for selector in self.course_row_selectors:
                for row in soup.select(selector):
                    payload = self._parse_freeform_row(row)
                    if payload and (payload.department_code, payload.course_code) not in seen:
                        seen.add((payload.department_code, payload.course_code))
                        courses.append(payload)

        return courses

    def _parse_table(self, table: Any, seen: set[tuple[str, str]]) -> list[CoursePayload]:
        rows = table.find_all("tr")
        if not rows:
            return []

        header_map = self._build_header_map(rows[0])
        parsed: list[CoursePayload] = []
        for row in rows[1:] if header_map else rows:
            try:
                payload = self._parse_row_with_headers(row, header_map) if header_map else self._parse_freeform_row(row)
                if not payload:
                    continue
                key = (payload.department_code, payload.course_code)
                if key in seen:
                    continue
                seen.add(key)
                parsed.append(payload)
            except Exception as exc:
                print(f"Warning: skipped malformed course row: {exc}")
                continue
        return parsed

    def _build_header_map(self, header_row: Any) -> dict[int, str]:
        cells = header_row.find_all(["th", "td"])
        header_map: dict[int, str] = {}
        for index, cell in enumerate(cells):
            normalized = normalize_header(safe_text(cell))
            if normalized in COURSE_FIELD_KEYS:
                header_map[index] = normalized
        return header_map

    def _parse_row_with_headers(self, row: Any, header_map: dict[int, str]) -> CoursePayload | None:
        cells = row.find_all("td")
        if len(cells) < 2:
            return None

        values: dict[str, str] = {}
        syllabus_url: str | None = None
        for index, cell in enumerate(cells):
            key = header_map.get(index)
            if not key:
                continue
                values[key] = safe_text(cell, default="")
            if key == "syllabus":
                syllabus_url = self._extract_link(cell)

        return self._payload_from_values(values, syllabus_url=syllabus_url)

    def _parse_freeform_row(self, row: Any) -> CoursePayload | None:
        text_value = safe_text(row)
        if not maybe_course_row(text_value):
            return None

        cells = row.find_all(["td", "th"]) + row.select(".rwd_td")
        cell_texts = [safe_text(cell) for cell in cells if safe_text(cell)]
        values = self._extract_labeled_values(row)

        if "course_code" not in values:
            match = re.search(r"\b([A-Z]{2,8}\s*\d{3,5}[A-Z]?)\b", text_value.upper())
            if match:
                values["course_code"] = match.group(1).replace(" ", "")
        if "department" not in values and cell_texts:
            values["department"] = cell_texts[0]
        if "title" not in values and len(cell_texts) >= 2:
            values["title"] = self._guess_title(cell_texts)
        if "credits" not in values:
            credit_match = re.search(r"(?:學分|credits?)[:：\s]*(\d+(?:\.\d+)?)", text_value, re.I)
            if credit_match:
                values["credits"] = credit_match.group(1)
        if "required" not in values:
            required_match = re.search(r"(必修|選修|必選修|Required|Elective)", text_value, re.I)
            if required_match:
                values["required"] = required_match.group(1)
        if "instructor" not in values:
            teacher_match = re.search(r"(?:任課教師|授課教師|教師|teacher|instructor)[:：\s]*([^\s,，/／]+)", text_value, re.I)
            if teacher_match:
                values["instructor"] = teacher_match.group(1)
        if "syllabus" not in values:
            values["syllabus"] = text_value

        return self._payload_from_values(values, syllabus_url=self._extract_link(row))

    def _extract_labeled_values(self, row: Any) -> dict[str, str]:
        values: dict[str, str] = {}
        labeled_nodes = row.select("[data-title], [headers], .rwd_td")
        for node in labeled_nodes:
            label = node.get("data-title") or node.get("headers") or ""
            if not label:
                previous = node.find_previous(class_="rwd_th")
                label = safe_text(previous)
            key = normalize_header(str(label))
            if key in COURSE_FIELD_KEYS:
                values[key] = safe_text(node, default="")
        text_value = safe_text(row)
        for label, value in re.findall(r"([^:：\s]{2,8})[:：]\s*([^:：]+?)(?=\s+[^:：\s]{2,8}[:：]|$)", text_value):
            key = normalize_header(label)
            if key in COURSE_FIELD_KEYS:
                values.setdefault(key, value.strip())
        return values

    def _payload_from_values(self, values: dict[str, str], *, syllabus_url: str | None) -> CoursePayload | None:
        try:
            course_code = safe_field(values, "course_code").upper().replace(" ", "")
            title = safe_field(values, "title")
            if not course_code or not title:
                return None

            raw_department = safe_field(values, "department") or safe_field(values, "department_code")
            department_code = (
                safe_field(values, "department_code")
                or infer_department_code(raw_department, course_code)
            ).upper()
            department_name = infer_department_name(raw_department, department_code)
            required_text = safe_field(values, "required")
            time_room = safe_field(values, "time_room") or None
            required_for_major = infer_required(required_text)
            description = clean_text(values.get("syllabus"))
            if time_room:
                description = "\n".join(
                    part for part in [f"時間教室：{time_room}", description] if part
                )
            tags = extract_course_tags(
                title,
                required_text,
                time_room,
                description,
                required_for_major=required_for_major,
            )
            return CoursePayload(
                course_code=course_code,
                department_code=department_code,
                department_name=department_name,
                title_zh=title,
                instructor_name=safe_field(values, "instructor") or None,
                credits=parse_decimal(safe_field(values, "credits")),
                required_for_major=required_for_major,
                time_room=time_room,
                tags=tags,
                description=description,
                syllabus_url=syllabus_url,
                academic_year=self.academic_year,
                semester=self.semester,
            )
        except Exception as exc:
            logger.warning("skip malformed course payload values=%s error=%s", values, exc)
            return None

    def _extract_link(self, node: Any) -> str | None:
        try:
            link = node.find("a", href=True)
            if not link:
                return None
            return urljoin(self.base_url or "", link["href"])
        except Exception:
            return None

    @staticmethod
    def _guess_title(cell_texts: list[str]) -> str:
        for text_value in cell_texts:
            if not re.search(r"[A-Z]{2,8}\d{3,5}", text_value.upper()) and len(text_value) >= 2:
                if not any(marker in text_value for marker in ["必修", "選修", "學分"]):
                    return text_value
        return cell_texts[1] if len(cell_texts) > 1 else ""


def parse_course_html(
    html: str,
    *,
    academic_year: int | None,
    semester: int | None,
    base_url: str | None = None,
) -> list[CoursePayload]:
    """Parse mock/NCKU-like course table HTML into normalized course payloads."""

    return NckuCourseHtmlParser(
        academic_year=academic_year,
        semester=semester,
        base_url=base_url,
    ).parse(html)


def parse_course_json(payload: dict[str, Any], *, academic_year: int | None, semester: int | None) -> list[CoursePayload]:
    """Parse a JSON API response into normalized course payloads."""

    courses: list[CoursePayload] = []
    for item in payload.get("items", []):
        required_for_major = bool(item.get("required_for_major", False))
        time_room = str(item.get("time_room") or item.get("class_time_room") or "").strip() or None
        description = clean_text(item.get("syllabus_html") or item.get("description"))
        if time_room:
            description = "\n".join(part for part in [f"時間教室：{time_room}", description] if part)
        courses.append(
            CoursePayload(
                course_code=str(item["course_code"]).strip(),
                department_code=str(item["department_code"]).strip().upper(),
                department_name=str(item.get("department_name") or item["department_code"]).strip(),
                title_zh=str(item["title_zh"]).strip(),
                instructor_name=(str(item.get("instructor_name")).strip() if item.get("instructor_name") else None),
                credits=parse_decimal(item.get("credits")),
                required_for_major=required_for_major,
                time_room=time_room,
                tags=list(item.get("tags") or [])
                or extract_course_tags(
                    item.get("title_zh"),
                    item.get("description"),
                    time_room,
                    description,
                    required_for_major=required_for_major,
                ),
                description=description,
                syllabus_url=item.get("syllabus_url"),
                academic_year=item.get("academic_year", academic_year),
                semester=item.get("semester", semester),
            )
        )
    return courses


def parse_grade_json(payload: dict[str, Any]) -> list[GradePayload]:
    """Parse grade distribution JSON into normalized grade payloads."""

    grades: list[GradePayload] = []
    for item in payload.get("items", []):
        grades.append(
            GradePayload(
                course_code=str(item["course_code"]).strip(),
                department_code=str(item["department_code"]).strip().upper(),
                academic_year=int(item["academic_year"]),
                semester=int(item["semester"]),
                enrollment_count=(int(item["enrollment_count"]) if item.get("enrollment_count") is not None else None),
                a_plus_ratio=parse_decimal(item.get("a_plus_ratio")),
                fail_ratio=parse_decimal(item.get("fail_ratio")),
                source_url=item.get("source_url"),
            )
        )
    return grades


def parse_grade_html(html: str) -> list[GradePayload]:
    """Parse a simple grade table; useful when a forum/source has HTML tables."""

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("#grades tr")[1:]
    grades: list[GradePayload] = []

    for row in rows:
        cells = [cell.get_text(strip=True) for cell in row.find_all("td")]
        if len(cells) < 7:
            continue

        grades.append(
            GradePayload(
                course_code=cells[0],
                department_code=cells[1].upper(),
                academic_year=int(cells[2]),
                semester=int(cells[3]),
                enrollment_count=int(cells[4]),
                a_plus_ratio=parse_decimal(cells[5]),
                fail_ratio=parse_decimal(cells[6]),
                source_url=None,
            )
        )

    return grades


async def upsert_department(
    session: AsyncSession,
    *,
    department_code: str,
    department_name: str,
) -> UUID:
    stmt = (
        insert(Department)
        .values(
            code=department_code,
            name_zh=department_name,
            is_active=True,
        )
        .on_conflict_do_update(
            index_elements=[Department.code],
            set_={
                "name_zh": department_name,
                "is_active": True,
            },
        )
        .returning(Department.id)
    )
    return await session.scalar(stmt)


async def upsert_course(session: AsyncSession, payload: CoursePayload) -> UUID:
    department_id = await upsert_department(
        session,
        department_code=payload.department_code,
        department_name=payload.department_name,
    )

    stmt = (
        insert(Course)
        .values(
            department_id=department_id,
            course_code=payload.course_code,
            title_zh=payload.title_zh,
            instructor_name=payload.instructor_name,
            academic_year=payload.academic_year,
            semester=payload.semester,
            credits=payload.credits,
            required_for_major=payload.required_for_major,
            tags=payload.tags,
            syllabus_url=payload.syllabus_url,
            description=payload.description,
            difficulty=CourseDifficulty.UNKNOWN,
        )
        .on_conflict_do_update(
            constraint="uq_course_department_code",
            set_={
                "title_zh": payload.title_zh,
                "instructor_name": payload.instructor_name,
                "academic_year": payload.academic_year,
                "semester": payload.semester,
                "credits": payload.credits,
                "required_for_major": payload.required_for_major,
                "tags": payload.tags,
                "syllabus_url": payload.syllabus_url,
                "description": payload.description,
            },
        )
        .returning(Course.id)
    )
    return await session.scalar(stmt)


async def find_course_id(
    session: AsyncSession,
    *,
    department_code: str,
    course_code: str,
) -> UUID | None:
    stmt = (
        select(Course.id)
        .join(Department, Course.department_id == Department.id)
        .where(Department.code == department_code, Course.course_code == course_code)
    )
    return await session.scalar(stmt)


def build_grade_buckets(payload: GradePayload) -> dict[str, float | int | None]:
    """Store ratios in JSONB until the data model gets dedicated ratio columns."""

    return {
        "A+_ratio": float(payload.a_plus_ratio) if payload.a_plus_ratio is not None else None,
        "fail_ratio": float(payload.fail_ratio) if payload.fail_ratio is not None else None,
        "enrollment_count": payload.enrollment_count,
    }


async def upsert_grade_distribution(session: AsyncSession, payload: GradePayload) -> bool:
    course_id = await find_course_id(
        session,
        department_code=payload.department_code,
        course_code=payload.course_code,
    )
    if course_id is None:
        print(f"Skip grade: course not found {payload.department_code}/{payload.course_code}")
        return False

    pass_rate = None
    if payload.fail_ratio is not None:
        pass_rate = Decimal("1") - payload.fail_ratio

    stmt = (
        insert(CourseGradeDistribution)
        .values(
            course_id=course_id,
            academic_year=payload.academic_year,
            semester=payload.semester,
            enrollment_count=payload.enrollment_count,
            pass_rate=pass_rate,
            grade_buckets=build_grade_buckets(payload),
            source_url=payload.source_url,
        )
        .on_conflict_do_update(
            constraint="uq_course_grade_term",
            set_={
                "enrollment_count": payload.enrollment_count,
                "pass_rate": pass_rate,
                "grade_buckets": build_grade_buckets(payload),
                "source_url": payload.source_url,
            },
        )
    )
    await session.execute(stmt)
    return True


async def fetch_courses(
    http: PoliteHttpClient,
    *,
    mock: bool,
    course_url: str | None,
    academic_year: int | None,
    semester: int | None,
    department_code: str | None = None,
) -> list[CoursePayload]:
    if mock:
        return filter_courses_by_department(
            parse_course_html(MOCK_COURSES_HTML, academic_year=academic_year, semester=semester),
            department_code,
        )

    if not course_url:
        raise ValueError("--course-url is required when --mock is false")

    params = {
        key: value
        for key, value in {
            "academic_year": academic_year,
            "semester": semester,
        }.items()
        if value is not None
    }
    try:
        text = await http.get_text(course_url, params=params)
        iframe_url = extract_iframe_source(text, course_url)
        if iframe_url:
            text = await http.get_text(iframe_url, params=params)
    except FetchSkippedError as exc:
        logger.error("course source skipped: %s", exc)
        return []

    content_type = "json" if text.lstrip().startswith("{") else "html"
    if content_type == "json":
        courses = parse_course_json(httpx.Response(200, text=text).json(), academic_year=academic_year, semester=semester)
    else:
        courses = parse_course_html(text, academic_year=academic_year, semester=semester, base_url=iframe_url or course_url)

    courses = filter_courses_by_department(courses, department_code)

    return await enrich_course_syllabi(http, courses)


def filter_courses_by_department(
    courses: list[CoursePayload],
    department_code: str | None,
) -> list[CoursePayload]:
    if not department_code:
        return courses
    normalized_department_code = department_code.strip().upper()
    return [
        course
        for course in courses
        if course.department_code.upper() == normalized_department_code
    ]


async def enrich_course_syllabi(
    http: PoliteHttpClient,
    courses: list[CoursePayload],
) -> list[CoursePayload]:
    enriched: list[CoursePayload] = []
    for course in courses:
        if not course.syllabus_url:
            enriched.append(course)
            continue

        try:
            detail_html = await http.get_text(course.syllabus_url)
        except FetchSkippedError as exc:
            logger.error(
                "skip syllabus detail course=%s url=%s error=%s",
                course.course_code,
                course.syllabus_url,
                exc,
            )
            enriched.append(course)
            continue

        detail_text = clean_text(detail_html)
        if detail_text and len(detail_text) > len(course.description or ""):
            prefix = f"時間教室：{course.time_room}" if course.time_room else None
            enriched.append(
                replace(
                    course,
                    description="\n".join(
                        part for part in [prefix, detail_text] if part
                    ),
                )
            )
        else:
            enriched.append(course)
    return enriched


def extract_iframe_source(html: str, base_url: str) -> str | None:
    try:
        soup = BeautifulSoup(html, "html.parser")
        iframe = soup.find("iframe", src=True)
        if iframe and "course-query.acad.ncku.edu.tw" in iframe["src"]:
            return urljoin(base_url, iframe["src"])
    except Exception:
        return None
    return None


async def fetch_grades(
    http: PoliteHttpClient,
    *,
    mock: bool,
    grade_url: str | None,
) -> list[GradePayload]:
    if mock:
        return parse_grade_json(MOCK_GRADES_JSON)

    if not grade_url:
        print("Warning: --grade-url not provided; skipping grade distribution fetch.")
        return []

    try:
        text = await http.get_text(grade_url)
    except FetchSkippedError as exc:
        logger.error("grade source skipped: %s", exc)
        return []
    if text.lstrip().startswith("{"):
        return parse_grade_json(httpx.Response(200, text=text).json())
    return parse_grade_html(text)


async def run_pipeline(args: argparse.Namespace) -> None:
    database_url = getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    engine = create_async_engine(database_url, pool_pre_ping=True)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    if args.create_tables:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)

    http = PoliteHttpClient(
        concurrency=args.concurrency,
        min_interval_seconds=args.min_interval,
        jitter_seconds=args.jitter,
        timeout_seconds=args.timeout,
    )

    try:
        courses = await fetch_courses(
            http,
            mock=args.mock,
            course_url=args.course_url,
            academic_year=args.academic_year,
            semester=args.semester,
            department_code=args.department_code,
        )
        grades = await fetch_grades(
            http,
            mock=args.mock,
            grade_url=args.grade_url,
        )

        if args.validate_only:
            print(
                "Validation complete: "
                f"department_code={args.department_code or 'ALL'}, "
                f"courses_parsed={len(courses)}, "
                f"grades_parsed={len(grades)}"
            )
            for course in courses[: args.sample_limit]:
                print(
                    " - "
                    f"{course.department_code} {course.course_code} "
                    f"{course.title_zh} teacher={course.instructor_name or ''} "
                    f"credits={course.credits or ''} "
                    f"required={course.required_for_major} "
                    f"time_room={course.time_room or ''} "
                    f"tags={','.join(course.tags)}"
                )
            return

        async with async_session() as session:
            async with session.begin():
                course_ids = []
                for course in courses:
                    course_ids.append(await upsert_course(session, course))

                written_grades = 0
                for grade in grades:
                    if await upsert_grade_distribution(session, grade):
                        written_grades += 1

        print(
            "Pipeline complete: "
            f"courses_upserted={len(course_ids)}, "
            f"grade_distributions_upserted={written_grades}"
        )
    finally:
        await http.close()
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch and upsert NCKU course planning data.")
    parser.add_argument("--mock", action="store_true", help="Use built-in mock fixtures instead of real HTTP URLs.")
    parser.add_argument("--course-url", help="Course source URL. Supports JSON or table HTML.")
    parser.add_argument("--grade-url", help="Grade source URL. Supports JSON or table HTML.")
    parser.add_argument(
        "--department-code",
        help="Parse only one department code after fetching, e.g. DPS for Photonics.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Fetch and parse sources, print samples, and skip database writes.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=10,
        help="Number of parsed course samples printed in --validate-only mode.",
    )
    parser.add_argument("--academic-year", type=int, default=113, help="Academic year passed to course source.")
    parser.add_argument("--semester", type=int, default=1, help="Semester passed to course source.")
    parser.add_argument("--create-tables", action="store_true", help="Create DB tables before writing data.")
    parser.add_argument("--concurrency", type=int, default=2, help="Maximum concurrent HTTP requests.")
    parser.add_argument("--min-interval", type=float, default=1.5, help="Minimum seconds between requests.")
    parser.add_argument("--jitter", type=float, default=1.0, help="Random extra delay in seconds.")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout in seconds.")
    return parser


def main() -> None:
    logging.basicConfig(
        level=getenv("COURSE_PIPELINE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    args = build_parser().parse_args()
    if not args.mock and not args.course_url:
        raise SystemExit("Use --mock for local testing, or provide --course-url for real crawling.")

    asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    main()
