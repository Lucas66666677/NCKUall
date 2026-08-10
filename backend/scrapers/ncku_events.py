"""Scrape upcoming public events from NCKU Activity Registration System.

Outputs:
    data/upcoming_events.json

Sources:
    - https://activity.ncku.edu.tw/
    - Public AJAX endpoints used by the legacy frontend:
      - index.php?c=apply&m=canlendar
      - index.php?c=apply&m=read
      - index.php?c=apply&m=ajax_query&act_id=<id>

Examples:
    python backend/scrapers/ncku_events.py
    python backend/scrapers/ncku_events.py --max-events 20 --delay-min 0 --delay-max 0
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
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

LOGGER = logging.getLogger("ncku_events")

BASE_URL = "https://activity.ncku.edu.tw/"
CALENDAR_URL = urljoin(BASE_URL, "index.php?c=apply&m=canlendar")
LIST_URL = urljoin(BASE_URL, "index.php?c=apply&m=read")
DETAIL_URL = urljoin(BASE_URL, "index.php?c=apply&m=ajax_query")
DEFAULT_OUTPUT = "data/upcoming_events.json"
DEFAULT_TIMEOUT = 25
DEFAULT_RETRIES = 3
DEFAULT_DELAY_MIN = 0.35
DEFAULT_DELAY_MAX = 1.1
TAIPEI_TZ = ZoneInfo("Asia/Taipei")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("[學術演講]", ("演講", "講座", "研討", "論壇", "學術", "研究", "半導體", "光電", "教師資格")),
    ("[藝文活動]", ("藝文", "藝術", "展覽", "電影", "影展", "音樂", "舞蹈", "劇場", "博物館", "圖書館")),
    ("[社團展演]", ("社團", "展演", "表演", "迎新", "桌遊", "舞會", "音樂會", "活動中心")),
    ("[職涯活動]", ("職涯", "徵才", "實習", "求職", "企業", "就業", "履歷", "面試")),
    ("[課程工作坊]", ("課程", "工作坊", "培訓", "訓練", "AI", "ChatGPT", "Code", "研習")),
    ("[健康輔導]", ("心理", "諮商", "健康", "性別", "伴侶", "關懷", "衛生")),
]


def utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def taipei_now() -> dt.datetime:
    return dt.datetime.now(TAIPEI_TZ).replace(microsecond=0)


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    if "<" in text and ">" in text:
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
            "Referer": BASE_URL,
        }
    )
    return session


def sleep_random(min_seconds: float, max_seconds: float) -> None:
    if max_seconds <= 0:
        return
    time.sleep(random.uniform(max(min_seconds, 0), max(max_seconds, min_seconds, 0)))


def request_with_retries(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: int,
    retries: int,
    delay_min: float,
    delay_max: float,
    **kwargs: Any,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.request(method, url, timeout=timeout, **kwargs)
            if response.status_code in {403, 429, 500, 502, 503, 504}:
                raise requests.HTTPError(f"HTTP {response.status_code}: {url}", response=response)
            response.raise_for_status()
            response.encoding = "utf-8"
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= retries:
                break
            wait = (2 ** (attempt - 1)) + random.uniform(delay_min, delay_max)
            LOGGER.warning("%s failed (%s/%s): %s; retrying %.1fs", method, attempt, retries, exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"Failed {method} after {retries} attempts: {url}") from last_error


def parse_datetime(value: Any) -> dt.datetime | None:
    text = compact_text(value)
    if not text:
        return None
    normalized = text.replace("/", "-")
    match = re.search(r"(\d{4}-\d{1,2}-\d{1,2})[ T](\d{1,2}:\d{2})(?::\d{2})?", normalized)
    if not match:
        return None
    try:
        return dt.datetime.strptime(f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M").replace(tzinfo=TAIPEI_TZ)
    except ValueError:
        return None


def datetime_iso(value: dt.datetime | None) -> str:
    return value.isoformat() if value else ""


def classify_event(title: str, category: str, description: str = "") -> str:
    haystack = f"{title} {category} {description}".lower()
    for label, keywords in CATEGORY_RULES:
        if any(keyword.lower() in haystack for keyword in keywords):
            return label
    return "[校園活動]"


def parse_state(value: Any) -> str:
    return compact_text(value)


def parse_count_pair(value: str) -> tuple[int | None, int | None]:
    match = re.search(r"(\d+)\s*/\s*(\d+)", value.replace(",", ""))
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def parse_capacity(detail: dict[str, str]) -> dict[str, int | None]:
    registered_count: int | None = None
    total_capacity: int | None = None
    waitlist_count: int | None = None
    waitlist_capacity: int | None = None

    if "正取" in detail:
        registered_count, total_capacity = parse_count_pair(detail["正取"])
    if "備取" in detail:
        waitlist_count, waitlist_capacity = parse_count_pair(detail["備取"])

    for key in ("人數上限", "名額", "總名額"):
        if total_capacity is None and key in detail:
            match = re.search(r"\d+", detail[key].replace(",", ""))
            total_capacity = int(match.group(0)) if match else None

    remaining_slots: int | None = None
    if registered_count is not None and total_capacity is not None:
        remaining_slots = max(total_capacity - registered_count, 0)

    return {
        "registered_count": registered_count,
        "total_capacity": total_capacity,
        "remaining_slots": remaining_slots,
        "waitlist_count": waitlist_count,
        "waitlist_capacity": waitlist_capacity,
    }


def parse_detail_table(html_text: str) -> tuple[dict[str, str], str]:
    soup = BeautifulSoup(html_text, "html.parser")
    detail: dict[str, str] = {}
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        key = compact_text(cells[0].get_text(" ", strip=True))
        value = compact_text(" ".join(cell.get_text(" ", strip=True) for cell in cells[1:]))
        if key:
            detail[key] = value

    description_parts: list[str] = []
    for selector in ("#tabs-2", "#tabs-3", ".tab-pane"):
        for node in soup.select(selector):
            text = compact_text(node.get_text(" ", strip=True))
            if text and "活動資料" not in text:
                description_parts.append(text)
    description = max(description_parts, key=len, default="")
    return detail, description


def fetch_calendar_events(session: requests.Session, args: argparse.Namespace) -> list[dict[str, Any]]:
    data = {"adv_flt": "true", "ncku": ""}
    response = request_with_retries(
        session,
        "POST",
        CALENDAR_URL,
        timeout=args.timeout,
        retries=args.retries,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        data=data,
        headers={"X-Requested-With": "XMLHttpRequest", "Referer": BASE_URL},
    )
    payload = response.json()
    return payload if isinstance(payload, list) else []


def fetch_list_index(session: requests.Session, args: argparse.Namespace) -> dict[str, dict[str, str]]:
    data = {"adv_flt": "true", "ncku": "", "size": str(args.list_size)}
    response = request_with_retries(
        session,
        "POST",
        LIST_URL,
        timeout=args.timeout,
        retries=args.retries,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        data=data,
        headers={"X-Requested-With": "XMLHttpRequest", "Referer": BASE_URL},
    )
    soup = BeautifulSoup(response.text, "html.parser")
    index: dict[str, dict[str, str]] = {}
    for card in soup.select(".act-col-md-6"):
        onclick = " ".join(str(tag.get("onclick") or "") for tag in card.select("[onclick]"))
        match = re.search(r"(?:look_act|apply_act|cancel_signup)\((\d+)\)", onclick)
        if not match:
            continue
        event_id = match.group(1)
        apply_text = compact_text(card.select_one(".act-active-apply").get_text(" ", strip=True) if card.select_one(".act-active-apply") else "")
        index[event_id] = {
            "title": compact_text(card.select_one(".act-name").get_text(" ", strip=True) if card.select_one(".act-name") else ""),
            "category": compact_text(card.select_one(".act-cate-name").get_text(" ", strip=True) if card.select_one(".act-cate-name") else ""),
            "time_text": compact_text(card.select_one(".act-active-time").get_text(" ", strip=True) if card.select_one(".act-active-time") else ""),
            "location": compact_text(card.select_one(".act-place-en").get_text(" ", strip=True) if card.select_one(".act-place-en") else ""),
            "apply_text": apply_text,
            "registration_start": extract_labeled_datetime(apply_text, "報名開始"),
            "registration_deadline": extract_labeled_datetime(apply_text, "報名截止"),
            "status": compact_text(card.select_one("[class*=act-btn]").get_text(" ", strip=True) if card.select_one("[class*=act-btn]") else ""),
        }
    return index


def extract_labeled_datetime(text: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}[:：]\s*(\d{{4}}[-/]\d{{1,2}}[-/]\d{{1,2}}\s+\d{{1,2}}:\d{{2}})", text)
    parsed = parse_datetime(match.group(1)) if match else None
    return datetime_iso(parsed)


def fetch_detail(session: requests.Session, event_id: str, args: argparse.Namespace) -> tuple[dict[str, str], str, str | None]:
    try:
        response = request_with_retries(
            session,
            "GET",
            DETAIL_URL,
            timeout=args.timeout,
            retries=args.retries,
            delay_min=args.delay_min,
            delay_max=args.delay_max,
            params={"act_id": event_id},
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": BASE_URL},
        )
        if "error-active id" in response.text.lower() or "access error" in response.text.lower():
            return {}, "", "detail endpoint returned access/error marker"
        detail, description = parse_detail_table(response.text)
        return detail, description, None
    except Exception as exc:
        return {}, "", str(exc)


def merge_event(
    calendar_event: dict[str, Any],
    list_info: dict[str, str],
    detail: dict[str, str],
    description: str,
) -> dict[str, Any]:
    event_id = compact_text(calendar_event.get("id"))
    title = compact_text(detail.get("活動名稱") or list_info.get("title") or calendar_event.get("title"))
    category = compact_text(list_info.get("category") or detail.get("活動類型"))
    organizer = compact_text(detail.get("主辦單位") or detail.get("承辦單位"))
    location = compact_text(detail.get("活動地點") or list_info.get("location") or calendar_event.get("active_place"))
    start_at = parse_datetime(detail.get("活動開始") or calendar_event.get("start"))
    end_at = parse_datetime(detail.get("活動結束") or calendar_event.get("end"))
    registration_start = parse_datetime(detail.get("報名開始")) or parse_datetime(list_info.get("registration_start"))
    registration_deadline = parse_datetime(detail.get("報名結束") or detail.get("報名截止")) or parse_datetime(
        list_info.get("registration_deadline")
    )
    capacity = parse_capacity(detail)
    status = compact_text(list_info.get("status") or parse_state(calendar_event.get("state_info")))
    public_url = compact_text(detail.get("活動分享網址") or calendar_event.get("url")) or urljoin(
        BASE_URL, f"index.php?c=apply&no={event_id}"
    )
    normalized_description = description[:800] if description else ""

    return {
        "event_id": event_id,
        "event_name": title,
        "organizer": organizer,
        "category": category,
        "auto_category": classify_event(title, category, normalized_description),
        "date_time_text": compact_text(list_info.get("time_text") or calendar_event.get("active_time")),
        "start_at": datetime_iso(start_at),
        "end_at": datetime_iso(end_at),
        "location": location,
        "remaining_slots": capacity["remaining_slots"],
        "total_capacity": capacity["total_capacity"],
        "registered_count": capacity["registered_count"],
        "waitlist_count": capacity["waitlist_count"],
        "waitlist_capacity": capacity["waitlist_capacity"],
        "registration_start": datetime_iso(registration_start),
        "registration_deadline": datetime_iso(registration_deadline),
        "status": status,
        "language": compact_text(detail.get("主要語言別") or calendar_event.get("language")),
        "contact_name": compact_text(detail.get("承辦人聯絡資訊")),
        "contact_phone": compact_text(detail.get("承辦人電話")),
        "contact_email": compact_text(detail.get("承辦人信箱")),
        "external_link": compact_text(detail.get("相關網站")),
        "url": public_url,
        "description": normalized_description,
        "source": "ncku_activity_system",
        "scraped_at": utc_now_iso(),
        "raw": {"calendar": calendar_event, "list": list_info, "detail": detail},
    }


def is_upcoming(event: dict[str, Any], now: dt.datetime) -> bool:
    end_at = parse_datetime(event.get("end_at")) or parse_datetime(event.get("start_at"))
    if not end_at:
        return True
    return end_at >= now


def scrape(args: argparse.Namespace) -> dict[str, Any]:
    session = create_session()
    errors: list[dict[str, str]] = []
    calendar_events = fetch_calendar_events(session, args)
    list_index = fetch_list_index(session, args)
    now = parse_datetime(args.today) if args.today else taipei_now()
    if now is None:
        now = taipei_now()

    events: list[dict[str, Any]] = []
    for raw_event in calendar_events:
        if not isinstance(raw_event, dict):
            continue
        event_id = compact_text(raw_event.get("id"))
        start_at = parse_datetime(raw_event.get("start"))
        end_at = parse_datetime(raw_event.get("end")) or start_at
        if end_at and end_at < now:
            continue
        detail: dict[str, str] = {}
        description = ""
        if event_id and not args.skip_details:
            detail, description, error = fetch_detail(session, event_id, args)
            if error:
                errors.append({"event_id": event_id, "url": DETAIL_URL, "error": error})
            sleep_random(args.delay_min, args.delay_max)
        event = merge_event(raw_event, list_index.get(event_id, {}), detail, description)
        if is_upcoming(event, now):
            events.append(event)
        if args.max_events and len(events) >= args.max_events:
            break

    events.sort(key=lambda item: item.get("start_at") or item.get("end_at") or "")
    return {
        "source": "ncku_activity_system",
        "generated_at": utc_now_iso(),
        "source_urls": [BASE_URL, CALENDAR_URL, LIST_URL, DETAIL_URL],
        "total_count": len(events),
        "counts_by_auto_category": count_by(events, "auto_category"),
        "events": events,
        "errors": errors,
    }


def count_by(events: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        value = str(event.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def write_json(payload: dict[str, Any], output_path: str) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape upcoming NCKU public events.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON path.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="HTTP timeout seconds.")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="Retry count per request.")
    parser.add_argument("--delay-min", type=float, default=DEFAULT_DELAY_MIN, help="Minimum random delay.")
    parser.add_argument("--delay-max", type=float, default=DEFAULT_DELAY_MAX, help="Maximum random delay.")
    parser.add_argument("--list-size", type=int, default=120, help="List endpoint size used to enrich calendar rows.")
    parser.add_argument("--max-events", type=int, default=0, help="Optional cap for local smoke tests.")
    parser.add_argument("--skip-details", action="store_true", help="Skip per-event ajax_query detail enrichment.")
    parser.add_argument("--today", default="", help="Override current time, e.g. 2026-07-12 00:00.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logs.")
    return parser.parse_args(argv)


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(levelname)s %(message)s")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    configure_logging(args.verbose)
    payload = scrape(args)
    output_path = write_json(payload, args.output)
    LOGGER.info(
        "Wrote %s upcoming event(s) to %s; categories=%s; errors=%s",
        payload["total_count"],
        output_path,
        payload["counts_by_auto_category"],
        len(payload["errors"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
