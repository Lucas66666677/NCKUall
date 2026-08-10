"""Scrape NCKU Hub course ratings/comments for a local NCKU course JSON file.

Public NCKU Hub frontend code shows the current API shape:
    GET /course/              -> current semester courses
    GET /course/allCoursePrev -> historical course list
    GET /course/{id}          -> rating + comments for a course

The scraper reads a course catalog JSON such as data/ncku_courses_f7.json,
matches courses by title/code/department/teacher, skips courses without
comments, and writes normalized comment rows to data/nckuhub_reviews.json.

Examples:
    python backend/scrapers/nckuhub_reviews.py --input data/ncku_courses_f7.json
    python backend/scrapers/nckuhub_reviews.py --input data/ncku_courses_f7.json --max-courses 20
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

LOGGER = logging.getLogger("nckuhub_reviews")

DEFAULT_API_BASE = "https://nckuhub.com"
DEFAULT_INPUT = "data/ncku_courses_f7.json"
DEFAULT_OUTPUT = "data/nckuhub_reviews.json"
DEFAULT_TIMEOUT = 25
DEFAULT_RETRIES = 3
DEFAULT_DELAY_MIN = 0.8
DEFAULT_DELAY_MAX = 2.2
DEFAULT_MIN_MATCH_SCORE = 70

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]


def utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", "", text)
    text = text.replace("（", "(").replace("）", ")")
    return text


def normalize_teacher(value: Any) -> str:
    text = normalize_text(value)
    text = text.replace("*", "")
    return text


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://nckuhub.com/",
            "Origin": "https://nckuhub.com",
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


def request_json_with_retries(
    session: requests.Session,
    url: str,
    *,
    timeout: int,
    retries: int,
    delay_min: float,
    delay_max: float,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            if response.status_code in {403, 429, 500, 502, 503, 504}:
                raise requests.HTTPError(f"HTTP {response.status_code}: {url}", response=response)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "application/json" not in content_type and not response.text.lstrip().startswith(("{", "[")):
                raise ValueError(f"Expected JSON from {url}, got {content_type!r}")
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            wait_seconds = (2 ** (attempt - 1)) + random.uniform(delay_min, delay_max)
            LOGGER.warning(
                "NCKU Hub request failed (%s/%s): %s; retrying in %.1fs",
                attempt,
                retries,
                exc,
                wait_seconds,
            )
            time.sleep(wait_seconds)
    raise RuntimeError(f"Failed NCKU Hub request after {retries} attempts: {url}") from last_error


def read_input_courses(path: str | Path) -> list[dict[str, Any]]:
    input_path = Path(path)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    courses = payload.get("courses") if isinstance(payload, dict) else payload
    if not isinstance(courses, list):
        raise ValueError("Input JSON must contain a top-level 'courses' array or be an array.")
    return [course for course in courses if isinstance(course, dict)]


def fetch_nckuhub_course_index(
    session: requests.Session,
    *,
    api_base: str,
    include_history: bool,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    current_payload = request_json_with_retries(
        session,
        f"{api_base.rstrip('/')}/course/",
        timeout=args.timeout,
        retries=args.retries,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
    )
    current_courses = current_payload.get("courses", []) if isinstance(current_payload, dict) else []
    if not isinstance(current_courses, list):
        raise ValueError("NCKU Hub /course/ did not return a courses array.")

    courses = [course for course in current_courses if isinstance(course, dict)]
    if include_history:
        sleep_random(args.delay_min, args.delay_max)
        try:
            history_payload = request_json_with_retries(
                session,
                f"{api_base.rstrip('/')}/course/allCoursePrev",
                timeout=args.timeout,
                retries=args.retries,
                delay_min=args.delay_min,
                delay_max=args.delay_max,
            )
            if isinstance(history_payload, list):
                courses.extend(course for course in history_payload if isinstance(course, dict))
        except Exception as exc:
            LOGGER.warning("Historical NCKU Hub course index failed; continuing current only: %s", exc)

    deduped: dict[str, dict[str, Any]] = {}
    for course in courses:
        course_id = str(course.get("id") or "")
        if course_id:
            deduped[course_id] = course
    return list(deduped.values())


def input_course_title(course: dict[str, Any]) -> str:
    return str(course.get("title") or course.get("title_zh") or course.get("課程名稱") or "").strip()


def input_course_teacher(course: dict[str, Any]) -> str:
    return str(course.get("instructor_name") or course.get("teacher") or course.get("老師") or "").strip()


def input_course_code_candidates(course: dict[str, Any]) -> set[str]:
    keys = ["course_code", "raw_course_no", "selection_serial", "課程碼", "屬性碼"]
    candidates = {normalize_text(course.get(key)) for key in keys if course.get(key)}
    cleaned: set[str] = set()
    for candidate in candidates:
        if candidate:
            cleaned.add(candidate)
            cleaned.add(candidate.replace("-", ""))
    return cleaned


def input_department_candidates(course: dict[str, Any]) -> set[str]:
    keys = ["department_no", "department_abbreviation", "department_name", "department_label", "系號", "系所名稱"]
    return {normalize_text(course.get(key)) for key in keys if course.get(key)}


def hub_course_code_candidates(course: dict[str, Any]) -> set[str]:
    keys = ["課程碼", "屬性碼", "選課序號"]
    candidates = {normalize_text(course.get(key)) for key in keys if course.get(key)}
    cleaned: set[str] = set()
    for candidate in candidates:
        if candidate:
            cleaned.add(candidate)
            cleaned.add(candidate.replace("-", ""))
    return cleaned


def match_score(input_course: dict[str, Any], hub_course: dict[str, Any]) -> int:
    score = 0
    title = normalize_text(input_course_title(input_course))
    hub_title = normalize_text(hub_course.get("課程名稱"))
    if title and hub_title:
        if title == hub_title:
            score += 55
        elif title in hub_title or hub_title in title:
            score += 38

    input_codes = input_course_code_candidates(input_course)
    hub_codes = hub_course_code_candidates(hub_course)
    if input_codes and hub_codes and input_codes.intersection(hub_codes):
        score += 35

    teacher = normalize_teacher(input_course_teacher(input_course))
    hub_teacher = normalize_teacher(hub_course.get("老師"))
    if teacher and hub_teacher:
        if teacher == hub_teacher:
            score += 25
        elif teacher in hub_teacher or hub_teacher in teacher:
            score += 15

    input_depts = input_department_candidates(input_course)
    hub_depts = {
        normalize_text(hub_course.get("系號")),
        normalize_text(hub_course.get("系所名稱")),
    }
    if input_depts and hub_depts and input_depts.intersection(hub_depts):
        score += 15

    return score


def find_best_matches(
    input_courses: list[dict[str, Any]],
    hub_courses: list[dict[str, Any]],
    *,
    min_score: int,
    max_courses: int,
    only_with_comments: bool,
) -> list[tuple[dict[str, Any], dict[str, Any], int]]:
    candidates = [
        course
        for course in hub_courses
        if not only_with_comments or to_int(course.get("comment_num")) > 0
    ]
    matches: list[tuple[dict[str, Any], dict[str, Any], int]] = []
    for input_course in input_courses:
        if max_courses and len(matches) >= max_courses:
            break
        scored = [
            (hub_course, match_score(input_course, hub_course))
            for hub_course in candidates
        ]
        scored = [item for item in scored if item[1] >= min_score]
        scored.sort(key=lambda item: (item[1], to_int(item[0].get("comment_num"))), reverse=True)
        if scored:
            hub_course, score = scored[0]
            matches.append((input_course, hub_course, score))
    return matches


def fetch_course_detail(
    session: requests.Session,
    hub_course_id: Any,
    *,
    api_base: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    return request_json_with_retries(
        session,
        f"{api_base.rstrip('/')}/course/{hub_course_id}",
        timeout=args.timeout,
        retries=args.retries,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
    )


def normalize_review_rows(
    *,
    input_course: dict[str, Any],
    hub_course: dict[str, Any],
    match_score_value: int,
    detail: dict[str, Any],
) -> list[dict[str, Any]]:
    comments = detail.get("comment") if isinstance(detail, dict) else []
    if not isinstance(comments, list) or not comments:
        return []

    course_info = detail.get("courseInfo") if isinstance(detail.get("courseInfo"), dict) else hub_course
    rows: list[dict[str, Any]] = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        text = str(comment.get("comment") or "").strip()
        if not text:
            continue
        rows.append(
            {
                "source": "nckuhub",
                "hub_course_id": str(course_info.get("id") or hub_course.get("id") or ""),
                "hub_comment_id": comment.get("id"),
                "matched_score": match_score_value,
                "matched_input_course": {
                    "title": input_course_title(input_course),
                    "course_code": input_course.get("course_code"),
                    "raw_course_no": input_course.get("raw_course_no"),
                    "department_no": input_course.get("department_no"),
                    "instructor_name": input_course_teacher(input_course),
                },
                "course": {
                    "title": course_info.get("課程名稱"),
                    "teacher": course_info.get("老師"),
                    "department": course_info.get("系所名稱"),
                    "department_no": course_info.get("系號"),
                    "course_code": course_info.get("課程碼"),
                    "serial": course_info.get("選課序號"),
                    "credits": course_info.get("學分"),
                    "required_type": course_info.get("選必修"),
                    "time": course_info.get("時間"),
                    "classroom": course_info.get("教室"),
                },
                "ratings": {
                    "gain": to_float(detail.get("got")),
                    "sweet": to_float(detail.get("sweet")),
                    "cold": to_float(detail.get("cold")),
                    "rate_count": to_int(detail.get("rate_count")),
                },
                "semester": comment.get("semester"),
                "comment": text,
                "url": f"https://nckuhub.com/?course={course_info.get('id') or hub_course.get('id')}",
                "scraped_at": utc_now_iso(),
            }
        )
    return rows


def scrape_nckuhub_reviews(args: argparse.Namespace) -> dict[str, Any]:
    input_courses = read_input_courses(args.input)
    if args.max_input_courses:
        input_courses = input_courses[: args.max_input_courses]

    session = create_session()
    hub_courses = fetch_nckuhub_course_index(
        session,
        api_base=args.api_base,
        include_history=args.include_history,
        args=args,
    )
    matches = find_best_matches(
        input_courses,
        hub_courses,
        min_score=args.min_match_score,
        max_courses=args.max_courses,
        only_with_comments=args.only_with_comments,
    )

    reviews: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    detail_count = 0

    LOGGER.info(
        "Matched %s/%s input course(s) against %s NCKU Hub course rows.",
        len(matches),
        len(input_courses),
        len(hub_courses),
    )

    for input_course, hub_course, score in matches:
        if args.max_details and detail_count >= args.max_details:
            break
        hub_course_id = hub_course.get("id")
        if not hub_course_id:
            continue
        try:
            sleep_random(args.delay_min, args.delay_max)
            detail = fetch_course_detail(
                session,
                hub_course_id,
                api_base=args.api_base,
                args=args,
            )
            detail_count += 1
            rows = normalize_review_rows(
                input_course=input_course,
                hub_course=hub_course,
                match_score_value=score,
                detail=detail,
            )
            if rows:
                reviews.extend(rows)
            else:
                LOGGER.info("Course id=%s has no readable comments; skipped.", hub_course_id)
        except Exception as exc:
            LOGGER.warning("Failed NCKU Hub course detail id=%s: %s", hub_course_id, exc)
            errors.append(
                {
                    "hub_course_id": str(hub_course_id),
                    "input_title": input_course_title(input_course),
                    "error": str(exc),
                }
            )

    return {
        "source": "nckuhub",
        "generated_at": utc_now_iso(),
        "api_base": args.api_base,
        "input_file": args.input,
        "input_course_count": len(input_courses),
        "hub_course_count": len(hub_courses),
        "matched_course_count": len(matches),
        "detail_request_count": detail_count,
        "review_count": len(reviews),
        "reviews": reviews,
        "errors": errors,
    }


def write_json(payload: dict[str, Any], output: str) -> None:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    LOGGER.info("Wrote NCKU Hub reviews JSON to %s", output_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Course catalog JSON file.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON path.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--include-history", action="store_true", help="Also load /course/allCoursePrev index.")
    parser.add_argument("--only-with-comments", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--min-match-score", type=int, default=DEFAULT_MIN_MATCH_SCORE)
    parser.add_argument("--max-input-courses", type=int, default=0, help="Testing guardrail before matching.")
    parser.add_argument("--max-courses", type=int, default=0, help="Maximum matched courses to request details for.")
    parser.add_argument("--max-details", type=int, default=0, help="Maximum detail API calls; 0 means unlimited.")
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
        payload = scrape_nckuhub_reviews(args)
        write_json(payload, args.output)
        if payload["errors"]:
            LOGGER.warning("Completed with %s detail error(s).", len(payload["errors"]))
        return 0
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user.")
        return 130
    except Exception as exc:
        LOGGER.error("NCKU Hub scrape failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
