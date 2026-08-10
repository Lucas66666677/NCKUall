"""Import cold-start scraper JSON into Supabase with dry-run and upsert safety.

Required environment variables:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY

Examples:
    python backend/scripts/import_to_supabase.py --type courses --file data/ncku_courses_f7.json --dry-run
    python backend/scripts/import_to_supabase.py --type courses --file data/ncku_courses_detailed.json
    python backend/scripts/import_to_supabase.py --type courses --file data/ncku_courses_f7.json
    python backend/scripts/import_to_supabase.py --type ptt --file data/ptt_reviews.json
    python backend/scripts/import_to_supabase.py --type unified_reviews --file data/unified_reviews_enriched.json

The script uses the Supabase service-role key. Keep it server-side only and
never expose it to Next.js or browser code.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from dotenv import load_dotenv

LOGGER = logging.getLogger("import_to_supabase")

DEFAULT_BATCH_SIZE = 200
COURSE_TABLE = "courses"
PTT_REVIEW_TABLE = "ptt_reviews"
COURSE_REVIEW_TABLE = "course_reviews"
DEPARTMENT_TABLE = "departments"


@dataclass(slots=True)
class ImportSummary:
    table: str
    total_input: int
    total_ready: int
    total_written: int
    skipped: int


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_environment() -> None:
    """Load .env files without overriding already exported production vars."""
    load_dotenv(project_root() / ".env", override=False)
    load_dotenv(backend_root() / ".env", override=False)


Client = Any


def build_supabase_client() -> Client:
    load_environment()
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    if not supabase_url:
        raise RuntimeError("Missing SUPABASE_URL in environment or backend/.env.")
    if not service_role_key:
        raise RuntimeError("Missing SUPABASE_SERVICE_ROLE_KEY in environment or backend/.env.")

    parsed = urlparse(supabase_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("SUPABASE_URL must look like https://<project-ref>.supabase.co")

    try:
        from supabase import create_client
    except ImportError as exc:
        raise RuntimeError(
            "Missing supabase Python SDK. Install requirements.txt first."
        ) from exc

    return create_client(supabase_url, service_role_key)


def read_json_file(path: str | Path) -> dict[str, Any]:
    json_path = Path(path)
    if not json_path.exists():
        raise FileNotFoundError(f"JSON file does not exist: {json_path}")
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON file {json_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Importer expects a JSON object at the file root.")
    return payload


def print_preview(records: list[dict[str, Any]], *, title: str, total_input: int) -> None:
    print("\n" + "=" * 88)
    print(f"DRY RUN: {title}")
    print("=" * 88)
    print(f"Total input rows: {total_input}")
    print(f"Rows ready after normalization: {len(records)}")
    print("First 5 normalized rows:")
    print(json.dumps(records[:5], ensure_ascii=False, indent=2, default=str))
    print("=" * 88 + "\n")


def chunked(items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def as_decimal_string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.1")))
    except (InvalidOperation, ValueError):
        return None


def truthy_required(value: Any, required_type: str | None = None) -> bool:
    text = f"{value or ''} {required_type or ''}".lower()
    return "必修" in text or "required" in text


def compact_text(*parts: Any) -> str:
    lines = [str(part).strip() for part in parts if part not in (None, "")]
    return "\n".join(line for line in lines if line)


def as_rating_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return min(max(round(number), 1), 5)


def normalize_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def normalize_course_code(raw: dict[str, Any]) -> str:
    for key in ("course_code", "raw_course_no", "selection_serial"):
        value = str(raw.get(key) or "").strip()
        if value:
            return value[:64]
    title = str(raw.get("title") or raw.get("title_zh") or "untitled").strip()
    department_no = str(raw.get("department_no") or "UNKNOWN").strip()
    return f"{department_no}:{title}"[:64]


def collect_course_candidates(raw: dict[str, Any]) -> list[str]:
    candidates = [
        raw.get("department_no"),
        raw.get("department_abbreviation"),
        raw.get("department_name"),
        raw.get("department_label"),
    ]
    clean: list[str] = []
    for value in candidates:
        text = str(value or "").strip()
        if text:
            clean.append(text)
            if text.startswith("(") and ")" in text:
                clean.append(text.split(")", 1)[1].strip())
    return clean


def fetch_departments(client: Client) -> list[dict[str, Any]]:
    response = (
        client.table(DEPARTMENT_TABLE)
        .select("id,code,name_zh,name_en")
        .execute()
    )
    return list(response.data or [])


def match_department_id(
    raw_course: dict[str, Any],
    departments: list[dict[str, Any]],
) -> str | None:
    candidates = collect_course_candidates(raw_course)
    normalized_candidates = {candidate.lower() for candidate in candidates}

    for department in departments:
        code = str(department.get("code") or "").strip()
        if code and code.lower() in normalized_candidates:
            return str(department["id"])

    for department in departments:
        names = [
            str(department.get("name_zh") or "").strip(),
            str(department.get("name_en") or "").strip(),
        ]
        for name in names:
            if not name:
                continue
            if any(name in candidate or candidate in name for candidate in candidates):
                return str(department["id"])
    return None


def create_missing_department(client: Client, raw_course: dict[str, Any]) -> str:
    code = str(raw_course.get("department_no") or "UNKNOWN").strip()[:32] or "UNKNOWN"
    name_zh = str(raw_course.get("department_name") or raw_course.get("department_label") or code).strip()
    payload = {
        "code": code,
        "name_zh": name_zh[:120],
        "name_en": raw_course.get("department_abbreviation"),
        "college": None,
        "is_active": True,
    }
    response = (
        client.table(DEPARTMENT_TABLE)
        .upsert(payload, on_conflict="code")
        .execute()
    )
    data = response.data or []
    if data and data[0].get("id"):
        return str(data[0]["id"])

    lookup = client.table(DEPARTMENT_TABLE).select("id").eq("code", code).limit(1).execute()
    if lookup.data:
        return str(lookup.data[0]["id"])
    raise RuntimeError(f"Could not create or find department for code={code}")


def normalize_course_record(
    raw: dict[str, Any],
    *,
    department_id: str | None,
) -> dict[str, Any]:
    title = str(raw.get("title") or raw.get("title_zh") or "").strip()
    if not title:
        raise ValueError("Course row is missing title.")
    if not department_id:
        raise ValueError(f"Course {title!r} could not be matched to a department_id.")

    tags = ["cold_start", "official_ncku_course_catalog"]
    for key in ("category", "required_type", "department_no"):
        value = str(raw.get(key) or "").strip()
        if value:
            tags.append(value)

    syllabus = raw.get("syllabus") if isinstance(raw.get("syllabus"), dict) else {}
    grading_policy = syllabus.get("grading_policy") or raw.get("grading_policy")
    textbook = syllabus.get("textbook") or raw.get("textbook")
    course_description = (
        syllabus.get("course_description")
        or raw.get("course_description")
        or raw.get("description")
    )

    return {
        "department_id": department_id,
        "course_code": normalize_course_code(raw),
        "title_zh": title[:200],
        "title_en": None,
        "instructor_name": str(raw.get("instructor_name") or "").strip()[:120] or None,
        "academic_year": raw.get("academic_year"),
        "semester": raw.get("semester"),
        "credits": as_decimal_string(raw.get("credits")),
        "required_for_major": truthy_required(raw.get("required_for_major"), raw.get("required_type")),
        "tags": list(dict.fromkeys(tags)),
        "syllabus_url": raw.get("syllabus_url"),
        "description": compact_text(
            course_description,
            raw.get("notes"),
            f"Time/room: {raw.get('time_room')}" if raw.get("time_room") else "",
            f"Source: {raw.get('source_url')}" if raw.get("source_url") else "",
        )
        or None,
        "grading_policy": grading_policy if isinstance(grading_policy, dict) else {"raw": grading_policy} if grading_policy else None,
        "textbook": textbook,
        "difficulty": "UNKNOWN",
    }


def normalize_ptt_review_record(raw: dict[str, Any]) -> dict[str, Any]:
    url = str(raw.get("url") or "").strip()
    if not url:
        raise ValueError("PTT review row is missing URL.")
    title = str(raw.get("title") or "").strip()
    content = str(raw.get("content") or "").strip()
    if not title and not content:
        raise ValueError(f"PTT review {url} has no title/content.")

    return {
        "url": url,
        "source": str(raw.get("source") or "ptt")[:40],
        "board": str(raw.get("board") or "")[:80],
        "matched_keyword": str(raw.get("matched_keyword") or "")[:200] or None,
        "title": title,
        "author": str(raw.get("author") or "")[:120] or None,
        "posted_at_raw": str(raw.get("posted_at_raw") or "")[:120] or None,
        "nrec": str(raw.get("nrec") or "")[:32] or None,
        "content": content,
        "excerpt": str(raw.get("excerpt") or "") or None,
        "tags": raw.get("tags") if isinstance(raw.get("tags"), list) else [],
        "pushes": raw.get("pushes") if isinstance(raw.get("pushes"), list) else [],
        "metadata": {
            "scraped_at": raw.get("scraped_at"),
            "matched_keyword": raw.get("matched_keyword"),
        },
        "scraped_at": raw.get("scraped_at"),
    }


def fetch_courses(client: Client) -> list[dict[str, Any]]:
    response = (
        client.table(COURSE_TABLE)
        .select("id,course_code,title_zh,instructor_name,department_id")
        .execute()
    )
    return list(response.data or [])


def normalize_match_text(value: Any) -> str:
    return str(value or "").strip().lower().replace("（", "(").replace("）", ")")


def match_course_id(raw_review: dict[str, Any], courses: list[dict[str, Any]]) -> str | None:
    metadata = raw_review.get("metadata") if isinstance(raw_review.get("metadata"), dict) else {}
    explicit_code = (
        raw_review.get("course_code")
        or metadata.get("course_code")
        or metadata.get("raw_course_no")
    )
    if explicit_code:
        code = normalize_match_text(explicit_code)
        for course in courses:
            if normalize_match_text(course.get("course_code")) == code:
                return str(course["id"])

    haystack = normalize_match_text(
        "\n".join(
            str(part or "")
            for part in [
                raw_review.get("title"),
                raw_review.get("content"),
                raw_review.get("ai_summary"),
                metadata.get("matched_keyword"),
            ]
        )
    )
    scored: list[tuple[int, dict[str, Any]]] = []
    for course in courses:
        title = normalize_match_text(course.get("title_zh"))
        if not title or title not in haystack:
            continue
        score = len(title)
        teacher = normalize_match_text(course.get("instructor_name"))
        if teacher and teacher in haystack:
            score += 20
        scored.append((score, course))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return str(scored[0][1]["id"])


def normalize_unified_review_record(
    raw: dict[str, Any],
    *,
    course_id: str | None,
) -> dict[str, Any]:
    content = str(raw.get("content") or "").strip()
    if not content:
        raise ValueError("Unified review row is missing content.")
    if not course_id:
        raise ValueError("Unified review row could not be matched to a course_id.")

    source = str(raw.get("source") or "unknown").strip()[:40]
    external_id = str(raw.get("source_id") or raw.get("url") or "").strip()
    if not external_id:
        raise ValueError("Unified review row is missing source_id/url.")

    sweetness = normalize_float(raw.get("sweetness"))
    hardness = normalize_float(raw.get("hardness"))
    chillness = normalize_float(raw.get("chillness"))
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}

    return {
        "course_id": course_id,
        "reviewer_department_id": None,
        "author_user_id": None,
        "overall_rating": as_rating_int(sweetness),
        "workload_rating": as_rating_int(hardness),
        "difficulty_rating": as_rating_int(hardness),
        "grading_fairness_rating": as_rating_int(sweetness),
        "content": content,
        "tags": tags,
        "is_verified": False,
        "is_approved": True,
        "score": 0,
        "ai_spam_confidence": 0,
        "external_source": source,
        "external_id": external_id,
        "source_url": raw.get("url"),
        "source_title": raw.get("title"),
        "sweetness": sweetness,
        "hardness": hardness,
        "chillness": chillness,
        "ai_summary": raw.get("ai_summary"),
        "metadata_json": {
            **metadata,
            "ai_enrichment_error": raw.get("ai_enrichment_error"),
            "source_id": raw.get("source_id"),
        },
    }


def upsert_records(
    client: Client,
    *,
    table: str,
    records: list[dict[str, Any]],
    on_conflict: str,
    batch_size: int,
) -> int:
    written = 0
    for batch in chunked(records, batch_size):
        response = client.table(table).upsert(batch, on_conflict=on_conflict).execute()
        written += len(response.data or batch)
        LOGGER.info("Upserted %s row(s) into %s.", len(batch), table)
    return written


def import_courses(args: argparse.Namespace) -> ImportSummary:
    payload = read_json_file(args.file)
    raw_courses = payload.get("courses")
    if not isinstance(raw_courses, list):
        raise ValueError("Course JSON must contain a top-level 'courses' array.")

    client: Client | None = None
    departments: list[dict[str, Any]] = []
    if not args.dry_run:
        client = build_supabase_client()
        departments = fetch_departments(client)

    records: list[dict[str, Any]] = []
    skipped = 0
    department_cache: dict[str, str] = {}

    for raw in raw_courses:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        try:
            department_id: str | None = None
            if args.dry_run:
                department_id = "DRY_RUN_DEPARTMENT_ID"
            else:
                cache_key = "|".join(collect_course_candidates(raw))
                department_id = department_cache.get(cache_key) or match_department_id(raw, departments)
                if not department_id and args.create_missing_departments and client:
                    department_id = create_missing_department(client, raw)
                    departments = fetch_departments(client)
                if department_id:
                    department_cache[cache_key] = department_id
            records.append(normalize_course_record(raw, department_id=department_id))
        except ValueError as exc:
            skipped += 1
            LOGGER.warning("Skipping course row: %s", exc)

    if args.dry_run:
        print_preview(records, title="courses -> Supabase courses", total_input=len(raw_courses))
        return ImportSummary(COURSE_TABLE, len(raw_courses), len(records), 0, skipped)

    assert client is not None
    written = upsert_records(
        client,
        table=COURSE_TABLE,
        records=records,
        on_conflict="department_id,course_code",
        batch_size=args.batch_size,
    )
    return ImportSummary(COURSE_TABLE, len(raw_courses), len(records), written, skipped)


def import_ptt_reviews(args: argparse.Namespace) -> ImportSummary:
    payload = read_json_file(args.file)
    raw_reviews = payload.get("reviews")
    if not isinstance(raw_reviews, list):
        raise ValueError("PTT JSON must contain a top-level 'reviews' array.")

    records: list[dict[str, Any]] = []
    skipped = 0
    for raw in raw_reviews:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        try:
            records.append(normalize_ptt_review_record(raw))
        except ValueError as exc:
            skipped += 1
            LOGGER.warning("Skipping PTT review row: %s", exc)

    if args.dry_run:
        print_preview(records, title="ptt reviews -> Supabase ptt_reviews", total_input=len(raw_reviews))
        return ImportSummary(PTT_REVIEW_TABLE, len(raw_reviews), len(records), 0, skipped)

    client = build_supabase_client()
    written = upsert_records(
        client,
        table=PTT_REVIEW_TABLE,
        records=records,
        on_conflict="url",
        batch_size=args.batch_size,
    )
    return ImportSummary(PTT_REVIEW_TABLE, len(raw_reviews), len(records), written, skipped)


def import_unified_reviews(args: argparse.Namespace) -> ImportSummary:
    payload = read_json_file(args.file)
    raw_reviews = payload.get("reviews")
    if not isinstance(raw_reviews, list):
        raise ValueError("Unified reviews JSON must contain a top-level 'reviews' array.")

    client: Client | None = None
    courses: list[dict[str, Any]] = []
    if not args.dry_run:
        client = build_supabase_client()
        courses = fetch_courses(client)

    records: list[dict[str, Any]] = []
    skipped = 0
    for raw in raw_reviews:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        try:
            course_id = "DRY_RUN_COURSE_ID" if args.dry_run else match_course_id(raw, courses)
            records.append(normalize_unified_review_record(raw, course_id=course_id))
        except ValueError as exc:
            skipped += 1
            LOGGER.warning("Skipping unified review row: %s", exc)

    if args.dry_run:
        print_preview(
            records,
            title="unified enriched reviews -> Supabase course_reviews",
            total_input=len(raw_reviews),
        )
        return ImportSummary(COURSE_REVIEW_TABLE, len(raw_reviews), len(records), 0, skipped)

    assert client is not None
    written = upsert_records(
        client,
        table=COURSE_REVIEW_TABLE,
        records=records,
        on_conflict="external_source,external_id",
        batch_size=args.batch_size,
    )
    return ImportSummary(COURSE_REVIEW_TABLE, len(raw_reviews), len(records), written, skipped)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", choices=["courses", "ptt", "unified_reviews"], required=True)
    parser.add_argument("--file", required=True, help="Scraper JSON file to import.")
    parser.add_argument("--dry-run", action="store_true", help="Preview normalized rows without writing.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--create-missing-departments",
        action="store_true",
        help="For course imports, create placeholder departments when no match exists.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    try:
        if args.type == "courses":
            summary = import_courses(args)
        elif args.type == "ptt":
            summary = import_ptt_reviews(args)
        else:
            summary = import_unified_reviews(args)
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user.")
        return 130
    except Exception as exc:
        LOGGER.error("Import failed: %s", exc)
        return 1

    print(
        f"Import summary: table={summary.table}, input={summary.total_input}, "
        f"ready={summary.total_ready}, written={summary.total_written}, skipped={summary.skipped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
