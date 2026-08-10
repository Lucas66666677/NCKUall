"""Enrich NCKU course catalog JSON with official syllabus details.

The official syllabus pages usually use:
    https://class-qry.acad.ncku.edu.tw/syllabus/online_display.php
        ?class_code=<class_code>&co_no=<course_system_no>&sem=<semester>&syear=<0115>

This script reads data/ncku_courses_f7.json, tries existing syllabus_url first,
then derives candidate syllabus URLs from course code / raw course number fields.
It parses grading policy, textbook/references, and course description, then
writes a merged JSON file to data/ncku_courses_detailed.json.

Examples:
    python backend/scrapers/ncku_syllabus.py
    python backend/scrapers/ncku_syllabus.py --input data/ncku_courses_f7.json --max-courses 20
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import logging
import random
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from bs4 import BeautifulSoup

LOGGER = logging.getLogger("ncku_syllabus")

DEFAULT_INPUT = "data/ncku_courses_f7.json"
DEFAULT_OUTPUT = "data/ncku_courses_detailed.json"
DEFAULT_SYLLABUS_ENDPOINT = "https://class-qry.acad.ncku.edu.tw/syllabus/online_display.php"
DEFAULT_DELAY_MIN = 1.0
DEFAULT_DELAY_MAX = 3.0
DEFAULT_TIMEOUT = 25
DEFAULT_RETRIES = 3

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

SECTION_ORDER = [
    "開課系所 Department/Institute:",
    "開課教師 Instructor:",
    "開課學年 Academic Year:",
    "開課學期 Semester:",
    "開課序號 Serial Number:",
    "課程屬性碼Course No (Attribute Code):",
    "課程系統碼Course System Number:",
    "分班碼 Class Code:",
    "學分數 No. of Credits:",
    "課程語言 Medium of Instruction:",
    "課程網址 Course Website:",
    "先修課程或先備能力",
    "教師聯絡資訊 Contact with Teacher",
    "助教資訊 Contact with Tutor",
    "學習規範 Course Policy",
    "評量方式 Grading",
    "期末考試週規劃 Final Exam Week Schedule",
    "教學方法 Teaching Strategies",
    "課程教材 Course Material",
    "參考書目 References",
    "備註 Remarks",
    "基本素養 Basic Literacy",
    "核心能力 Competence",
    "課程概述 Course Description",
    "課程學習目標 Course Objectives",
    "課程進度 Progress Description",
]


def utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(value)
    text = BeautifulSoup(text, "html.parser").get_text("\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_line(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://course.ncku.edu.tw/",
            "Connection": "keep-alive",
        }
    )
    return session


def sleep_random(min_seconds: float, max_seconds: float) -> None:
    if max_seconds <= 0:
        return
    low = max(min_seconds, 0)
    high = max(max_seconds, low)
    time.sleep(random.uniform(low, high))


def request_html_with_retries(
    session: requests.Session,
    url: str,
    *,
    timeout: int,
    retries: int,
    delay_min: float,
    delay_max: float,
) -> str:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            if response.status_code in {403, 429, 500, 502, 503, 504}:
                raise requests.HTTPError(f"HTTP {response.status_code}: {url}", response=response)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            if is_robot_check(response.text):
                raise RuntimeError("robot-check page detected")
            return response.text
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            wait_seconds = (2 ** (attempt - 1)) + random.uniform(delay_min, delay_max)
            LOGGER.warning(
                "Syllabus request failed (%s/%s): %s; retrying in %.1fs",
                attempt,
                retries,
                exc,
                wait_seconds,
            )
            time.sleep(wait_seconds)
    raise RuntimeError(f"Failed syllabus request after {retries} attempts: {url}") from last_error


def is_robot_check(html_text: str) -> bool:
    lowered = html_text.lower()
    return any(
        marker in lowered
        for marker in [
            "robotcheck",
            "請輸入驗證碼",
            "cf-error-details",
            "cloudflare",
            "access denied",
        ]
    )


def read_course_json(path: str | Path) -> dict[str, Any]:
    input_path = Path(path)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {"courses": payload}
    if not isinstance(payload, dict) or not isinstance(payload.get("courses"), list):
        raise ValueError("Input JSON must contain a top-level 'courses' array or be an array.")
    return payload


def format_syear(value: Any, default_year: int) -> str:
    raw = str(value or "").strip()
    if raw.startswith("0") and len(raw) == 4:
        return raw
    if raw.isdigit():
        return f"{int(raw):04d}"
    return f"{default_year:04d}"


def infer_semester(course: dict[str, Any], default_semester: int) -> str:
    for key in ("semester", "sem", "開課學期"):
        value = str(course.get(key) or "").strip()
        if value in {"1", "2", "3"}:
            return value
    return str(default_semester)


def split_course_number(value: Any) -> tuple[str | None, str]:
    text = str(value or "").strip()
    if not text:
        return None, ""
    text = text.split("[", 1)[0].strip()
    if "-" in text:
        co_no, class_code = text.split("-", 1)
        return co_no.strip() or None, class_code.strip()
    return text, ""


def candidate_course_numbers(course: dict[str, Any]) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    raw_fields = [
        course.get("syllabus_co_no"),
        course.get("raw_course_no"),
        course.get("course_system_number"),
        course.get("課程系統碼"),
        course.get("課程碼"),
        course.get("course_code"),
        course.get("selection_serial"),
    ]
    explicit_class_code = str(course.get("class_code") or course.get("分班碼") or "").strip()
    for value in raw_fields:
        co_no, class_code = split_course_number(value)
        if co_no:
            candidates.append((co_no, class_code or explicit_class_code))

    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for co_no, class_code in candidates:
        key = (co_no, class_code)
        if key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


def build_syllabus_url(endpoint: str, *, co_no: str, class_code: str, sem: str, syear: str) -> str:
    params = {
        "class_code": class_code,
        "co_no": co_no,
        "sem": sem,
        "syear": syear,
    }
    return f"{endpoint}?{urlencode(params)}"


def existing_syllabus_url(course: dict[str, Any]) -> str | None:
    url = str(course.get("syllabus_url") or "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.scheme:
        return None
    return url


def build_candidate_urls(course: dict[str, Any], args: argparse.Namespace) -> list[str]:
    urls: list[str] = []
    existing = existing_syllabus_url(course)
    if existing:
        urls.append(existing)

    syear = format_syear(course.get("academic_year") or course.get("syear"), args.default_syear)
    sem = infer_semester(course, args.default_semester)
    for co_no, class_code in candidate_course_numbers(course):
        urls.append(
            build_syllabus_url(
                args.endpoint,
                co_no=co_no,
                class_code=class_code,
                sem=sem,
                syear=syear,
            )
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def page_has_syllabus_content(soup: BeautifulSoup) -> bool:
    text = soup.get_text("\n", strip=True)
    if "課程大綱" not in text:
        return False
    if "查無資料" in text or "無此課程" in text:
        return False
    return "課程概述" in text or "評量方式" in text or "課程教材" in text


def extract_lines(soup: BeautifulSoup) -> list[str]:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = clean_text(soup.get_text("\n", strip=True))
    return [line.strip() for line in text.splitlines() if line.strip()]


def section_start_index(lines: list[str], labels: list[str]) -> int | None:
    normalized_labels = [normalize_line(label) for label in labels]
    for index, line in enumerate(lines):
        normalized = normalize_line(line)
        if any(label in normalized for label in normalized_labels):
            return index
    return None


def next_section_index(lines: list[str], start: int) -> int:
    normalized_headers = [normalize_line(header) for header in SECTION_ORDER]
    for index in range(start + 1, len(lines)):
        normalized = normalize_line(lines[index])
        if any(header in normalized for header in normalized_headers):
            return index
    return len(lines)


def extract_section(lines: list[str], labels: list[str], *, drop_first_line: bool = True) -> str:
    start = section_start_index(lines, labels)
    if start is None:
        return ""
    end = next_section_index(lines, start)
    section_lines = lines[start + 1 : end] if drop_first_line else lines[start:end]
    return clean_text("\n".join(section_lines))


def parse_grading(lines: list[str]) -> dict[str, Any]:
    raw = extract_section(lines, ["評量方式 Grading", "Grading"])
    items: list[dict[str, Any]] = []
    raw_lines = [line for line in raw.splitlines() if line.strip()]
    skip_words = {"方法", "百分比%", "百分比", "%"}
    index = 0
    while index < len(raw_lines):
        method = raw_lines[index].strip()
        percent = raw_lines[index + 1].strip() if index + 1 < len(raw_lines) else ""
        if method not in skip_words and re.fullmatch(r"\d+(?:\.\d+)?", percent):
            items.append({"method": method, "percentage": float(percent)})
            index += 2
        else:
            index += 1
    return {"raw": raw, "items": items}


def parse_syllabus_page(html_text: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")
    if not page_has_syllabus_content(soup):
        raise ValueError("HTML does not look like a valid NCKU syllabus page.")
    lines = extract_lines(soup)
    course_material = extract_section(lines, ["課程教材 Course Material", "Course Material"])
    references = extract_section(lines, ["參考書目 References", "References"])
    textbook = clean_text(
        "\n\n".join(
            part
            for part in [
                f"課程教材 Course Material:\n{course_material}" if course_material else "",
                f"參考書目 References:\n{references}" if references else "",
            ]
            if part
        )
    )
    return {
        "syllabus_url": url,
        "fetched_at": utc_now_iso(),
        "grading_policy": parse_grading(lines),
        "textbook": textbook,
        "course_material": course_material,
        "references": references,
        "course_description": extract_section(
            lines,
            ["課程概述 Course Description", "Course Description"],
        ),
    }


def enrich_courses(payload: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    session = create_session()
    courses = payload["courses"]
    enriched_count = 0
    errors: list[dict[str, Any]] = []

    target_courses = courses[: args.max_courses] if args.max_courses else courses
    for index, course in enumerate(target_courses, start=1):
        if not isinstance(course, dict):
            continue
        title = course.get("title") or course.get("title_zh") or course.get("課程名稱") or "(untitled)"
        candidate_urls = build_candidate_urls(course, args)
        if not candidate_urls:
            errors.append({"index": index, "title": title, "error": "no syllabus URL candidates"})
            continue

        LOGGER.info("[%s/%s] Fetching syllabus for %s", index, len(target_courses), title)
        syllabus_detail: dict[str, Any] | None = None
        last_error = ""
        for url in candidate_urls:
            try:
                sleep_random(args.delay_min, args.delay_max)
                html_text = request_html_with_retries(
                    session,
                    url,
                    timeout=args.timeout,
                    retries=args.retries,
                    delay_min=args.delay_min,
                    delay_max=args.delay_max,
                )
                syllabus_detail = parse_syllabus_page(html_text, url)
                break
            except Exception as exc:
                last_error = str(exc)
                LOGGER.debug("Candidate syllabus URL failed for %s: %s", title, exc)

        if syllabus_detail:
            course["syllabus"] = syllabus_detail
            course["syllabus_url"] = syllabus_detail["syllabus_url"]
            enriched_count += 1
        else:
            course.setdefault("syllabus", {})
            errors.append(
                {
                    "index": index,
                    "title": title,
                    "candidate_urls": candidate_urls,
                    "error": last_error or "all syllabus URL candidates failed",
                }
            )

    payload["syllabus_enrichment"] = {
        "generated_at": utc_now_iso(),
        "input_file": args.input,
        "course_count": len(courses),
        "attempted_count": len(target_courses),
        "enriched_count": enriched_count,
        "error_count": len(errors),
        "errors": errors,
    }
    return payload


def write_json(payload: dict[str, Any], output: str) -> None:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    LOGGER.info("Wrote detailed course JSON to %s", output_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--endpoint", default=DEFAULT_SYLLABUS_ENDPOINT)
    parser.add_argument("--default-syear", type=int, default=115, help="NCKU academic year, e.g. 115 -> 0115.")
    parser.add_argument("--default-semester", type=int, default=1)
    parser.add_argument("--max-courses", type=int, default=0, help="Testing guardrail; 0 means all courses.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--delay-min", type=float, default=DEFAULT_DELAY_MIN)
    parser.add_argument("--delay-max", type=float, default=DEFAULT_DELAY_MAX)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    try:
        payload = read_course_json(args.input)
        enriched = enrich_courses(payload, args)
        write_json(enriched, args.output)
        summary = enriched["syllabus_enrichment"]
        print(
            "Syllabus enrichment summary: "
            f"attempted={summary['attempted_count']}, "
            f"enriched={summary['enriched_count']}, "
            f"errors={summary['error_count']}"
        )
        return 0
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user.")
        return 130
    except Exception as exc:
        LOGGER.error("Syllabus enrichment failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
