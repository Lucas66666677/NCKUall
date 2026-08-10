"""Scrape NCKU career-center and campus job-fair information.

Outputs a unified JSON file:
    data/career_events.json

Primary sources:
    - NCKU Career Center: https://grad-osa.ncku.edu.tw/
    - NCKU job fair portal: https://nckujob.osa.ncku.edu.tw/index.php?c=portal

Examples:
    python backend/scrapers/career_fairs.py
    python backend/scrapers/career_fairs.py --pages 3 --output data/career_events.json
    python backend/scrapers/career_fairs.py --pages 1 --max-events 50 --verbose
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
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

LOGGER = logging.getLogger("career_fairs")

DEFAULT_OUTPUT = "data/career_events.json"
DEFAULT_TIMEOUT = 25
DEFAULT_RETRIES = 3
DEFAULT_DELAY_MIN = 0.5
DEFAULT_DELAY_MAX = 1.6

CAREER_CENTER_URL = "https://grad-osa.ncku.edu.tw/"
JOB_PORTAL_URL = "https://nckujob.osa.ncku.edu.tw/index.php?c=portal"
JOB_BASE_URL = "https://nckujob.osa.ncku.edu.tw/index.php"
SOUTH_FAIR_CONTENT_URL = f"{JOB_BASE_URL}?c=job11232"
SOUTH_FAIR_COMPANY_URL = f"{JOB_BASE_URL}?c=job11241"
AUTUMN_RECRUIT_COMPANY_URL = f"{JOB_BASE_URL}?c=job12231"
GENERAL_JOB_BOARD_URL = f"{JOB_BASE_URL}?c=job13211"

COMPANY_LIST_ENDPOINTS = [
    {
        "code": "job11241",
        "tab": "A",
        "title": "南區就業博覽會參展企業",
        "url": SOUTH_FAIR_COMPANY_URL,
    },
    {
        "code": "job12231",
        "tab": "A",
        "title": "秋季校園徵才參展企業",
        "url": AUTUMN_RECRUIT_COMPANY_URL,
    },
]

JOB_LIST_ENDPOINTS = [
    {
        "code": "job13211",
        "tab": "B",
        "title": "企業求才網職缺",
        "url": GENERAL_JOB_BOARD_URL,
    },
    {
        "code": "job11241",
        "tab": "B",
        "title": "南區就業博覽會職缺",
        "url": SOUTH_FAIR_COMPANY_URL,
    },
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

INTERNSHIP_KEYWORDS = ("實習", "intern", "internship", "工讀", "part-time", "兼職")


def utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = BeautifulSoup(text, "html.parser").get_text("\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", clean_text(value)).strip()


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
            "Connection": "keep-alive",
            "Referer": JOB_PORTAL_URL,
        }
    )
    return session


def sleep_random(min_seconds: float, max_seconds: float) -> None:
    if max_seconds <= 0:
        return
    time.sleep(random.uniform(max(min_seconds, 0), max(max_seconds, min_seconds, 0)))


def fetch_html(
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
            response.encoding = "utf-8" if "nckujob.osa.ncku.edu.tw" in url else response.apparent_encoding or "utf-8"
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= retries:
                break
            wait = (2 ** (attempt - 1)) + random.uniform(delay_min, delay_max)
            LOGGER.warning("GET failed (%s/%s): %s; retrying %.1fs", attempt, retries, exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"Failed GET after {retries} attempts: {url}") from last_error


def fetch_json_page(
    session: requests.Session,
    *,
    code: str,
    tab: str,
    page: int,
    timeout: int,
    retries: int,
    delay_min: float,
    delay_max: float,
) -> dict[str, Any]:
    """Read the same JSON endpoint used by NCKU job portal's frontend grid."""
    url = f"{JOB_BASE_URL}?c={code}&m=read_json&tab={tab}"
    payload = {"page": str(page), "code": code}
    # The portal's CSRF filter is sensitive to hand-written Accept/Content-Type
    # headers. requests will add a valid form Content-Type by itself, so keep
    # this close to the browser request that the frontend grid accepts.
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{JOB_BASE_URL}?c={code}",
    }
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            # The portal invalidates the session after some JSON reads. Use a
            # fresh short-lived session for each page while preserving UA style.
            request_session = create_session()
            request_session.headers.update({"User-Agent": session.headers.get("User-Agent", USER_AGENTS[0])})
            response = request_session.post(url, data=payload, headers=headers, timeout=timeout)
            if response.status_code in {403, 429, 500, 502, 503, 504}:
                raise requests.HTTPError(f"HTTP {response.status_code}: {url}", response=response)
            response.raise_for_status()
            response.encoding = "utf-8"
            body = response.json()
            if isinstance(body, dict) and body.get("success") is False:
                raise RuntimeError(f"NCKU job portal returned success=false: {body.get('msg')}")
            data = body.get("data", body) if isinstance(body, dict) else {}
            return data if isinstance(data, dict) else {}
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            wait = (2 ** (attempt - 1)) + random.uniform(delay_min, delay_max)
            LOGGER.warning(
                "JSON page failed code=%s tab=%s page=%s (%s/%s): %s; retrying %.1fs",
                code,
                tab,
                page,
                attempt,
                retries,
                exc,
                wait,
            )
            time.sleep(wait)
    raise RuntimeError(f"Failed JSON page after {retries} attempts: {url} page={page}") from last_error


def roc_date_to_iso(value: str) -> str:
    match = re.search(r"(?P<year>\d{2,3})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日", value)
    if not match:
        return ""
    year = int(match.group("year")) + 1911
    month = int(match.group("month"))
    day = int(match.group("day"))
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return ""


def extract_roc_dates(value: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for match in re.finditer(r"\d{2,3}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日(?:\([^)]+\))?", value):
        raw = compact_text(match.group(0))
        results.append({"raw": raw, "iso": roc_date_to_iso(raw)})
    return results


def extract_time_ranges(value: str) -> list[str]:
    return [
        re.sub(r"\s*~\s*", " ~ ", item).strip()
        for item in re.findall(r"\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2}", value)
    ]


def get_lines(html_text: str) -> list[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = clean_text(soup.get_text("\n", strip=True))
    return [line.strip() for line in text.splitlines() if line.strip()]


def find_value_after_label(lines: list[str], labels: tuple[str, ...], window: int = 5) -> str:
    for index, line in enumerate(lines):
        if any(label in line for label in labels):
            tail = [item for item in lines[index + 1 : index + 1 + window] if item not in labels]
            if tail:
                return compact_text(" ".join(tail))
    return ""


def extract_links(html_text: str, base_url: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html_text, "html.parser")
    links: list[dict[str, str]] = []
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        links.append({"text": compact_text(anchor.get_text(" ", strip=True)), "url": urljoin(base_url, href)})
    return links


def parse_job_fair_content(html_text: str, source_url: str) -> list[dict[str, Any]]:
    lines = get_lines(html_text)
    page_text = "\n".join(lines)
    links = extract_links(html_text, source_url)
    dates = extract_roc_dates(page_text)
    time_ranges = extract_time_ranges(page_text)

    booth_map_links = [
        link["url"]
        for link in links
        if "攤位" in link["text"] or re.search(r"\.(?:jpg|jpeg|png|pdf)(?:\?|$)", link["url"], re.I)
    ]
    session_table_links = [
        link["url"]
        for link in links
        if "場次" in link["text"] or "說明會" in link["text"] or "program.104.com.tw" in link["url"]
    ]

    fair_date = dates[0] if dates else {"raw": "", "iso": ""}
    location = find_value_after_label(lines, ("活動地點", "地點"), window=8)
    if not location:
        location = "光復校區中正堂、雲平大道、學生活動中心周邊"

    events: list[dict[str, Any]] = [
        {
            "source": "ncku_job_fair",
            "source_name": "成大就業博覽會專屬網站",
            "company_name": "國立成功大學",
            "event_type": "博覽會",
            "title": "南區就業博覽會",
            "date": fair_date["iso"],
            "date_text": fair_date["raw"],
            "time": ", ".join(time_ranges[:4]),
            "location": location,
            "link": source_url,
            "booth_map_links": list(dict.fromkeys(booth_map_links)),
            "registration_links": list(dict.fromkeys(session_table_links)),
            "description": "成大校園大型就業博覽會活動總覽，含現場徵才、企業說明會與攤位配置資訊。",
            "raw": {"dates": dates, "time_ranges": time_ranges},
            "scraped_at": utc_now_iso(),
        }
    ]

    briefing_dates = dates[1:] if len(dates) > 1 else dates
    briefing_time = ", ".join(time_ranges[-3:] if len(time_ranges) >= 3 else time_ranges)
    briefing_location = "光復校區學生活動中心 / 國際會議廳第一、第二、第三演講室"
    for date_item in briefing_dates[:3]:
        events.append(
            {
                "source": "ncku_job_fair",
                "source_name": "成大就業博覽會專屬網站",
                "company_name": "國立成功大學",
                "event_type": "說明會",
                "title": "企業徵才說明會",
                "date": date_item["iso"],
                "date_text": date_item["raw"],
                "time": briefing_time,
                "location": briefing_location,
                "link": session_table_links[0] if session_table_links else source_url,
                "booth_map_links": [],
                "registration_links": list(dict.fromkeys(session_table_links)),
                "description": "企業徵才說明會場次總覽；實際公司場次以來源網站公告為準。",
                "raw": {"dates": dates, "time_ranges": time_ranges},
                "scraped_at": utc_now_iso(),
            }
        )
    return events


def clean_tags(*values: Any) -> list[str]:
    tags: list[str] = []
    for value in values:
        text = compact_text(value)
        if text:
            tags.append(text)
    return list(dict.fromkeys(tags))


def parse_company_row(row: dict[str, Any], *, title: str, source_url: str) -> dict[str, Any]:
    booth = compact_text(row.get("render"))
    tags = clean_tags(row.get("indus"), row.get("is_intern"), row.get("is_oversea"), row.get("is_phd"))
    return {
        "source": "ncku_job_portal",
        "source_name": title,
        "company_name": compact_text(row.get("company_name")),
        "company_name_en": compact_text(row.get("company_name_en")),
        "event_type": "博覽會",
        "title": title,
        "date": "",
        "date_text": "",
        "time": "",
        "location": booth,
        "booth": booth,
        "link": source_url,
        "industry": compact_text(row.get("indus")),
        "tags": tags,
        "is_recruiting_interns": "實習" in compact_text(row.get("is_intern")),
        "job_count": int(row.get("job_cnt") or 0),
        "description": compact_text(row.get("operation")),
        "raw": row,
        "scraped_at": utc_now_iso(),
    }


def parse_job_row(row: dict[str, Any], *, title: str, source_url: str) -> dict[str, Any]:
    content = compact_text(row.get("job_content"))
    job_title = compact_text(row.get("job_title"))
    job_nature = compact_text(row.get("job_nature"))
    searchable = " ".join([job_title, content, job_nature]).lower()
    is_internship = any(keyword.lower() in searchable for keyword in INTERNSHIP_KEYWORDS)
    job_no = compact_text(row.get("job_no"))
    link = f"{source_url}#job-{job_no}" if job_no else source_url
    return {
        "source": "ncku_job_portal",
        "source_name": title,
        "company_name": compact_text(row.get("company_name")),
        "company_name_en": compact_text(row.get("company_name_en")),
        "event_type": "實習" if is_internship else "職缺",
        "title": job_title,
        "date": compact_text(row.get("upddate")),
        "date_text": compact_text(row.get("upddate")),
        "time": "",
        "location": compact_text(row.get("work_location")),
        "link": link,
        "industry": compact_text(row.get("indus")),
        "job_no": job_no,
        "job_nature": job_nature,
        "education_requirement": compact_text(row.get("edu_require")),
        "experience_requirement": compact_text(row.get("work_exper")),
        "compensation": compact_text(row.get("comp_cat")),
        "tags": clean_tags(row.get("indus"), job_nature, row.get("edu_require")),
        "description": content,
        "raw": row,
        "scraped_at": utc_now_iso(),
    }


def scrape_companies(
    session: requests.Session,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    events: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for endpoint in COMPANY_LIST_ENDPOINTS:
        for page in range(1, args.pages + 1):
            try:
                data = fetch_json_page(
                    session,
                    code=endpoint["code"],
                    tab=endpoint["tab"],
                    page=page,
                    timeout=args.timeout,
                    retries=args.retries,
                    delay_min=args.delay_min,
                    delay_max=args.delay_max,
                )
                rows = data.get("data") if isinstance(data, dict) else []
                if not rows:
                    break
                events.extend(
                    parse_company_row(row, title=endpoint["title"], source_url=endpoint["url"])
                    for row in rows
                    if isinstance(row, dict)
                )
                LOGGER.info(
                    "Fetched %s company rows from %s page %s/%s",
                    len(rows),
                    endpoint["code"],
                    page,
                    data.get("total_page", "?"),
                )
                sleep_random(args.delay_min, args.delay_max)
            except Exception as exc:
                LOGGER.warning("Company endpoint failed %s page %s: %s", endpoint["code"], page, exc)
                errors.append({"url": endpoint["url"], "page": str(page), "error": str(exc)})
                break
    return events, errors


def scrape_jobs(
    session: requests.Session,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    events: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for endpoint in JOB_LIST_ENDPOINTS:
        for page in range(1, args.pages + 1):
            try:
                data = fetch_json_page(
                    session,
                    code=endpoint["code"],
                    tab=endpoint["tab"],
                    page=page,
                    timeout=args.timeout,
                    retries=args.retries,
                    delay_min=args.delay_min,
                    delay_max=args.delay_max,
                )
                rows = data.get("data") if isinstance(data, dict) else []
                if not rows:
                    break
                parsed = [
                    parse_job_row(row, title=endpoint["title"], source_url=endpoint["url"])
                    for row in rows
                    if isinstance(row, dict)
                ]
                if args.only_internships:
                    parsed = [item for item in parsed if item["event_type"] == "實習"]
                events.extend(parsed)
                LOGGER.info(
                    "Fetched %s job rows from %s page %s/%s",
                    len(rows),
                    endpoint["code"],
                    page,
                    data.get("total_page", "?"),
                )
                sleep_random(args.delay_min, args.delay_max)
            except Exception as exc:
                LOGGER.warning("Job endpoint failed %s page %s: %s", endpoint["code"], page, exc)
                errors.append({"url": endpoint["url"], "page": str(page), "error": str(exc)})
                break
    return events, errors


def scrape_static_job_fair(
    session: requests.Session,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    try:
        html_text = fetch_html(
            session,
            SOUTH_FAIR_CONTENT_URL,
            timeout=args.timeout,
            retries=args.retries,
            delay_min=args.delay_min,
            delay_max=args.delay_max,
        )
        return parse_job_fair_content(html_text, SOUTH_FAIR_CONTENT_URL), []
    except Exception as exc:
        LOGGER.warning("Static job-fair content failed: %s", exc)
        return [], [{"url": SOUTH_FAIR_CONTENT_URL, "error": str(exc)}]


def event_dedupe_key(event: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    return (
        str(event.get("event_type") or ""),
        str(event.get("company_name") or ""),
        str(event.get("title") or ""),
        str(event.get("date") or event.get("date_text") or ""),
        str(event.get("location") or ""),
        str(event.get("job_no") or event.get("booth") or event.get("link") or ""),
    )


def dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for event in events:
        key = event_dedupe_key(event)
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    return unique


def build_payload(events: list[dict[str, Any]], errors: list[dict[str, str]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("event_type") or "unknown")
        counts[event_type] = counts.get(event_type, 0) + 1
    return {
        "source": "ncku_career_fairs",
        "generated_at": utc_now_iso(),
        "source_urls": [
            CAREER_CENTER_URL,
            JOB_PORTAL_URL,
            SOUTH_FAIR_CONTENT_URL,
            SOUTH_FAIR_COMPANY_URL,
            AUTUMN_RECRUIT_COMPANY_URL,
            GENERAL_JOB_BOARD_URL,
        ],
        "total_count": len(events),
        "counts_by_event_type": counts,
        "events": events,
        "errors": errors,
    }


def scrape(args: argparse.Namespace) -> dict[str, Any]:
    session = create_session()
    events: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    static_events, static_errors = scrape_static_job_fair(session, args)
    events.extend(static_events)
    errors.extend(static_errors)
    sleep_random(args.delay_min, args.delay_max)

    company_events, company_errors = scrape_companies(session, args)
    events.extend(company_events)
    errors.extend(company_errors)

    job_events, job_errors = scrape_jobs(session, args)
    events.extend(job_events)
    errors.extend(job_errors)

    events = dedupe_events(events)
    if args.max_events:
        events = events[: args.max_events]
    return build_payload(events, errors)


def write_json(payload: dict[str, Any], output_path: str) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape NCKU career fair and internship information.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON path.")
    parser.add_argument("--pages", type=int, default=3, help="Number of pages to crawl per list endpoint.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="HTTP timeout seconds.")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="Retry count per request.")
    parser.add_argument("--delay-min", type=float, default=DEFAULT_DELAY_MIN, help="Minimum random delay.")
    parser.add_argument("--delay-max", type=float, default=DEFAULT_DELAY_MAX, help="Maximum random delay.")
    parser.add_argument("--max-events", type=int, default=0, help="Optional cap for quick local tests.")
    parser.add_argument(
        "--only-internships",
        action="store_true",
        help="Keep only internship-like rows from job endpoints; fair company rows are still included.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logs.")
    return parser.parse_args(argv)


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    configure_logging(args.verbose)
    payload = scrape(args)
    output_path = write_json(payload, args.output)
    LOGGER.info(
        "Wrote %s event(s) to %s; counts=%s; errors=%s",
        payload["total_count"],
        output_path,
        payload["counts_by_event_type"],
        len(payload["errors"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
