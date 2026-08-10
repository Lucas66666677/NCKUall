"""Scrape the public NCKU course catalog into a JSON course dictionary.

This scraper uses the public "Quick Overview of all Courses" flow exposed by
course-query.acad.ncku.edu.tw. It does not log in and does not touch enrollment
actions. Keep delays conservative because NCKU explicitly warns students not to
interfere with the enrollment system.

Examples:
    python backend/scrapers/ncku_course_catalog.py --departments F7 --max-courses 20
    python backend/scrapers/ncku_course_catalog.py --limit-departments 3 --output courses.json
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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

LOGGER = logging.getLogger("ncku_course_catalog")

DEFAULT_BASE_URL = "https://course-query.acad.ncku.edu.tw/query/index.php"
DEFAULT_TIMEOUT = 30
DEFAULT_DELAY_SECONDS = 1.0

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]


@dataclass(slots=True)
class DepartmentOption:
    dept_no: str
    label: str
    name: str
    abbreviation: str | None = None


def utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def clean_text(value: str | None) -> str:
    """Normalize text for stable JSON/import and future embedding generation."""
    if not value:
        return ""
    text = html.unescape(value)
    text = BeautifulSoup(text, "html.parser").get_text("\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def cell_text(cell: Tag | None) -> str:
    if cell is None:
        return ""
    try:
        return clean_text(cell.get_text("\n", strip=True))
    except Exception as exc:  # pragma: no cover - defensive against broken DOMs
        LOGGER.debug("Failed to read table cell text: %s", exc)
        return ""


def first_href(cell: Tag | None, contains: str | None = None) -> str | None:
    if cell is None:
        return None
    try:
        for anchor in cell.select("a[href]"):
            href = str(anchor.get("href") or "").strip()
            if not href:
                continue
            if contains and contains not in href:
                continue
            return href
    except Exception as exc:  # pragma: no cover
        LOGGER.debug("Failed to read href: %s", exc)
    return None


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
            "Connection": "keep-alive",
        }
    )
    return session


def request_with_retries(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = 3,
    backoff_base: float = 1.0,
    **kwargs: Any,
) -> requests.Response:
    """HTTP wrapper with exponential backoff for transient school-server errors."""
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = session.request(method, url, timeout=timeout, **kwargs)
            if response.status_code in {403, 429, 500, 502, 503, 504}:
                raise requests.HTTPError(
                    f"HTTP {response.status_code} from {url}", response=response
                )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt == max_retries:
                break
            wait_seconds = backoff_base * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            LOGGER.warning(
                "Request failed (%s/%s): %s; retrying in %.1fs",
                attempt,
                max_retries,
                exc,
                wait_seconds,
            )
            time.sleep(wait_seconds)
    raise RuntimeError(f"Failed request after {max_retries} attempts: {url}") from last_error


def parse_department_label(raw_label: str, fallback_dept_no: str) -> DepartmentOption:
    text = clean_text(raw_label)
    match = re.match(r"^\((?P<dept>[^)]+)\)(?P<body>.+)$", text)
    if not match:
        return DepartmentOption(dept_no=fallback_dept_no, label=text, name=text)

    dept_no = match.group("dept").strip()
    body = match.group("body").strip()
    parts = body.rsplit(" ", 1)
    if len(parts) == 2 and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", parts[1]):
        name, abbreviation = parts[0].strip(), parts[1].strip()
    else:
        name, abbreviation = body, None
    return DepartmentOption(dept_no=dept_no, label=text, name=name, abbreviation=abbreviation)


def fetch_department_index(
    session: requests.Session,
    *,
    base_url: str,
    timeout: int,
) -> tuple[list[DepartmentOption], str]:
    response = request_with_retries(
        session,
        "GET",
        f"{base_url}?c=qry_all&lang=cht",
        timeout=timeout,
    )
    response.encoding = response.apparent_encoding or "utf-8"
    html_text = response.text

    if "請輸入驗證碼" in html_text or "RobotCheck" in html_text:
        raise RuntimeError(
            "NCKU course catalog returned a robot-check page. "
            "Slow down, retry later, or use a browser-exported HTML page for parsing."
        )

    crypt_match = re.search(r"'crypt'\s*:\s*'([^']+)'", html_text)
    if not crypt_match:
        raise ValueError(
            "Could not find qry_all crypt token in course catalog page. "
            "The public course-query DOM may have changed."
        )
    crypt = crypt_match.group(1)

    soup = BeautifulSoup(html_text, "html.parser")
    departments: list[DepartmentOption] = []
    for item in soup.select("li.btn_dept[data-dept]"):
        dept_no = str(item.get("data-dept") or "").strip()
        if not dept_no:
            continue
        departments.append(parse_department_label(item.get_text(" ", strip=True), dept_no))

    deduped: dict[str, DepartmentOption] = {}
    for department in departments:
        deduped.setdefault(department.dept_no, department)
    departments = list(deduped.values())

    if not departments:
        raise ValueError("Could not find department buttons in NCKU course catalog page.")
    return departments, crypt


def init_department_result(
    session: requests.Session,
    *,
    base_url: str,
    dept_no: str,
    crypt: str,
    timeout: int,
) -> str:
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://course-query.acad.ncku.edu.tw",
        "Referer": f"{base_url}?c=qry_all&lang=cht",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    response = request_with_retries(
        session,
        "POST",
        f"{base_url}?c=qry_all&m=result_init",
        timeout=timeout,
        headers=headers,
        data={"dept_no": dept_no, "crypt": crypt},
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError(f"Non-JSON result_init response for department {dept_no}") from exc

    if payload.get("err"):
        raise RuntimeError(f"NCKU course catalog returned error for {dept_no}: {payload['err']}")
    result_id = str(payload.get("id") or "").strip()
    if not result_id:
        raise ValueError(f"Missing result id for department {dept_no}: {payload}")
    return result_id


def fetch_department_result(
    session: requests.Session,
    *,
    base_url: str,
    result_id: str,
    timeout: int,
) -> str:
    response = request_with_retries(
        session,
        "GET",
        f"{base_url}?c=qry_all&m=result&i={result_id}",
        timeout=timeout,
    )
    response.encoding = response.apparent_encoding or "utf-8"
    if "請輸入驗證碼" in response.text or "RobotCheck" in response.text:
        raise RuntimeError(
            "NCKU course catalog returned a robot-check page while loading results."
        )
    return response.text


def parse_sequence_cell(value: str) -> dict[str, str | None]:
    compact = clean_text(value).replace("\n", " ")
    bracket_match = re.search(r"\[([^\]]+)\]", compact)
    first_token = compact.split(" ", 1)[0].strip() if compact else ""
    raw_course_no = first_token.split("[", 1)[0].strip()
    return {
        "selection_serial": first_token or None,
        "raw_course_no": raw_course_no or None,
        "course_code": bracket_match.group(1).strip() if bracket_match else None,
    }


def parse_credit_required(value: str) -> tuple[float | None, str]:
    text = clean_text(value)
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    credits = float(match.group(1)) if match else None
    required_type = "required" if "必修" in text else "elective" if "選修" in text else ""
    return credits, required_type


def parse_enrollment(value: str) -> tuple[int | None, int | None]:
    text = clean_text(value)
    match = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def parse_title_and_notes(cell: Tag | None) -> tuple[str, str]:
    if cell is None:
        return "", ""
    try:
        title_node = cell.select_one(".course_name") or cell.select_one("a")
        title = cell_text(title_node) if title_node else ""
        full = cell_text(cell)
        if not title:
            title = full.split("\n", 1)[0].strip()
        notes = full.replace(title, "", 1).strip() if title else ""
        return clean_text(title), clean_text(notes)
    except Exception as exc:  # pragma: no cover
        LOGGER.debug("Failed to parse title cell: %s", exc)
        return cell_text(cell), ""


def parse_course_tables(
    html_text: str,
    *,
    department: DepartmentOption,
    source_url: str,
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html_text, "html.parser")
    courses: list[dict[str, Any]] = []

    if "錯誤訊息" in soup.get_text(" ", strip=True) and "不存在" in soup.get_text(" ", strip=True):
        raise RuntimeError("NCKU returned an error page instead of a course result page.")

    for table in soup.select("table"):
        headers = [cell_text(th) for th in table.select("thead th")]
        if headers and not any("科目名稱" in header for header in headers):
            continue

        rows = table.select("tbody tr") or table.select("tr")
        for row in rows:
            cells = row.find_all("td", recursive=False)
            if len(cells) < 9:
                continue

            try:
                dept_name = cell_text(cells[0])
                sequence_info = parse_sequence_cell(cell_text(cells[1]))
                grade_class_group = cell_text(cells[2])
                category = cell_text(cells[3])
                title, notes = parse_title_and_notes(cells[4])
                credits, required_type = parse_credit_required(cell_text(cells[5]))
                instructor_name = cell_text(cells[6]).replace("*", "").strip()
                enrollment_text = cell_text(cells[7])
                selected_count, capacity = parse_enrollment(enrollment_text)
                time_room = cell_text(cells[8])
                syllabus_cell = cells[9] if len(cells) > 9 else None
            except (AttributeError, IndexError, ValueError) as exc:
                LOGGER.warning("Skipping malformed course row in %s: %s", department.dept_no, exc)
                continue

            if not title and not sequence_info["raw_course_no"]:
                continue

            courses.append(
                {
                    "source": "ncku_course_query_qry_all",
                    "source_url": source_url,
                    "scraped_at": utc_now_iso(),
                    "department_no": department.dept_no,
                    "department_label": department.label,
                    "department_name": dept_name or department.name,
                    "department_abbreviation": department.abbreviation,
                    "selection_serial": sequence_info["selection_serial"],
                    "raw_course_no": sequence_info["raw_course_no"],
                    "course_code": sequence_info["course_code"],
                    "grade_class_group": grade_class_group,
                    "category": category,
                    "title": title,
                    "instructor_name": instructor_name,
                    "credits": credits,
                    "required_type": required_type,
                    "enrollment_text": enrollment_text,
                    "selected_count": selected_count,
                    "capacity": capacity,
                    "time_room": time_room,
                    "notes": notes,
                    "course_map_url": first_href(cells[4], "course-map"),
                    "syllabus_url": first_href(syllabus_cell, "syllabus"),
                }
            )

    if not courses and "科目名稱" not in soup.get_text(" ", strip=True):
        LOGGER.warning(
            "No course table was found. The official page may be JS-only or robot-checked today."
        )

    return courses


def scrape_course_catalog(args: argparse.Namespace) -> dict[str, Any]:
    session = create_session()
    departments, crypt = fetch_department_index(
        session,
        base_url=args.base_url,
        timeout=args.timeout,
    )

    requested = {dept.strip() for dept in args.departments.split(",") if dept.strip()}
    if requested:
        departments = [department for department in departments if department.dept_no in requested]
    if args.limit_departments:
        departments = departments[: args.limit_departments]

    LOGGER.info("Preparing to scrape %s department(s).", len(departments))
    all_courses: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for index, department in enumerate(departments, start=1):
        if args.max_courses and len(all_courses) >= args.max_courses:
            break

        try:
            LOGGER.info("[%s/%s] Fetching %s %s", index, len(departments), department.dept_no, department.name)
            result_id = init_department_result(
                session,
                base_url=args.base_url,
                dept_no=department.dept_no,
                crypt=crypt,
                timeout=args.timeout,
            )
            time.sleep(max(args.delay, 0))
            html_text = fetch_department_result(
                session,
                base_url=args.base_url,
                result_id=result_id,
                timeout=args.timeout,
            )
            source_url = f"{args.base_url}?c=qry_all&m=result&i={result_id}"
            courses = parse_course_tables(html_text, department=department, source_url=source_url)
            if args.max_courses:
                remaining = max(args.max_courses - len(all_courses), 0)
                courses = courses[:remaining]
            all_courses.extend(courses)
            LOGGER.info("Parsed %s course row(s) from %s.", len(courses), department.dept_no)
        except Exception as exc:  # keep the cold-start crawl moving
            LOGGER.error("Department %s failed: %s", department.dept_no, exc)
            errors.append({"department_no": department.dept_no, "error": str(exc)})
        time.sleep(max(args.delay, 0))

    return {
        "source": "ncku_course_query_qry_all",
        "generated_at": utc_now_iso(),
        "department_count": len(departments),
        "course_count": len(all_courses),
        "departments": [asdict(department) for department in departments],
        "courses": all_courses,
        "errors": errors,
    }


def write_json(payload: dict[str, Any], output: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        LOGGER.info("Wrote JSON to %s", path)
        return
    print(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--departments", default="", help="Comma-separated department IDs, e.g. F7,DPS,E2")
    parser.add_argument("--limit-departments", type=int, default=0)
    parser.add_argument("--max-courses", type=int, default=0, help="Testing guardrail; 0 means unlimited.")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--output", default="", help="Write JSON to this path; stdout if omitted.")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    try:
        payload = scrape_course_catalog(args)
        write_json(payload, args.output or None)
        return 0
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user.")
        return 130
    except Exception as exc:
        LOGGER.error("Course catalog scrape failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
