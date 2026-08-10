"""Scrape NCKU campus-life essentials: gym/pool hours and dormitory news.

Outputs a unified JSON file:
    data/campus_life_updates.json

Sources:
    - NCKU PE office gym/pool opening hours
    - NCKU Housing Service Division announcements

Examples:
    python backend/scrapers/campus_life.py
    python backend/scrapers/campus_life.py --max-news 20 --output data/campus_life_updates.json
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

LOGGER = logging.getLogger("campus_life")

DEFAULT_OUTPUT = "data/campus_life_updates.json"
DEFAULT_TIMEOUT = 20
DEFAULT_RETRIES = 3
DEFAULT_DELAY_MIN = 0.6
DEFAULT_DELAY_MAX = 1.8

PE_HOURS_URLS = [
    "https://pe-acad.ncku.edu.tw/p/406-1045-201827%2Cr2330.php?Lang=zh-tw",
    "https://pe-acad.ncku.edu.tw/p/450-1045-29939,c1.php?Lang=zh-tw",
]

DORM_NEWS_URLS = [
    "https://housing-osa.ncku.edu.tw/p/403-1052-406.php?Lang=zh-tw",
    "https://housing-osa.ncku.edu.tw/p/403-1052-406-1.php?Lang=zh-tw",
    "https://housing-osa.ncku.edu.tw/p/403-1052-407.php?Lang=zh-tw",
]

IMPORTANT_KEYWORDS = ["繳費", "床位", "候補", "補宿", "志工", "住宿申請", "進住", "離宿"]
CRITICAL_KEYWORDS = ["緊急", "停班停課", "截止", "期限", "抽籤", "繳費"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]


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
            response.encoding = response.apparent_encoding or "utf-8"
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= retries:
                break
            wait = (2 ** (attempt - 1)) + random.uniform(delay_min, delay_max)
            LOGGER.warning("Request failed (%s/%s): %s; retrying %.1fs", attempt, retries, exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"Failed request after {retries} attempts: {url}") from last_error


def extract_text_lines(html_text: str) -> list[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return [line.strip() for line in clean_text(soup.get_text("\n", strip=True)).splitlines() if line.strip()]


def normalize_time(value: str) -> str:
    return re.sub(r"\s*~\s*", "~", value.strip())


def find_times(text: str) -> list[str]:
    return [normalize_time(item) for item in re.findall(r"\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2}", text)]


def parse_facility_hours_from_lines(lines: list[str], facility: str) -> dict[str, Any]:
    candidate_starts = [
        index
        for index, line in enumerate(lines)
        if line == facility and find_times("\n".join(lines[index : index + 14]))
    ]
    if not candidate_starts:
        return {
            "facility": facility,
            "status": "unknown",
            "hours": [],
            "clearing_periods": [],
            "notes": "facility label not found on source page",
        }
    start = candidate_starts[0]

    next_facilities = ["健身房", "室內游泳池", "室外游泳池", "健康休閒中心", "瀏覽數", "最後更新日期"]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if any(label in lines[index] for label in next_facilities if label != facility):
            end = index
            break
    block_lines = lines[start:end]
    block = "\n".join(block_lines)
    times = find_times(block)

    weekday_times: list[str] = []
    holiday_times: list[str] = []
    if facility == "健身房" and len(times) >= 5:
        weekday_times = times[:3]
        holiday_times = times[3:5]
    elif facility == "室內游泳池" and len(times) >= 4:
        weekday_times = times[:2]
        holiday_times = times[2:4]
    elif facility == "室外游泳池" and len(times) >= 3:
        weekday_times = times[:2]
        holiday_times = times[2:3]
    elif times:
        weekday_times = times[: max(1, len(times) // 2)]
        holiday_times = times[len(weekday_times) :]

    clearing_periods = infer_clearing_periods(weekday_times + holiday_times)
    return {
        "facility": facility,
        "status": "open" if times else "unknown",
        "hours": [
            {"days": "週一至週五", "time_ranges": weekday_times},
            {"days": "假日", "time_ranges": holiday_times},
        ],
        "clearing_periods": clearing_periods,
        "notes": clean_text(block),
    }


def infer_clearing_periods(time_ranges: list[str]) -> list[str]:
    """Infer gaps between adjacent same-day opening ranges as clearing/rest periods."""
    ranges: list[tuple[int, int, str]] = []
    for value in time_ranges:
        match = re.fullmatch(r"(\d{1,2}):(\d{2})~(\d{1,2}):(\d{2})", value)
        if not match:
            continue
        sh, sm, eh, em = map(int, match.groups())
        ranges.append((sh * 60 + sm, eh * 60 + em, value))
    ranges.sort()
    gaps: list[str] = []
    for (_, previous_end, _), (next_start, _, _) in zip(ranges, ranges[1:]):
        if next_start > previous_end:
            gaps.append(f"{previous_end // 60:02d}:{previous_end % 60:02d}~{next_start // 60:02d}:{next_start % 60:02d}")
    return list(dict.fromkeys(gaps))


def scrape_facility_hours(session: requests.Session, args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    for url in PE_HOURS_URLS:
        try:
            html_text = fetch_html(
                session,
                url,
                timeout=args.timeout,
                retries=args.retries,
                delay_min=args.delay_min,
                delay_max=args.delay_max,
            )
            lines = extract_text_lines(html_text)
            facilities = [
                parse_facility_hours_from_lines(lines, "健身房"),
                parse_facility_hours_from_lines(lines, "室內游泳池"),
                parse_facility_hours_from_lines(lines, "室外游泳池"),
            ]
            # User-facing alias requested by the product spec.
            if facilities:
                facilities[0]["facility"] = "新館健身房"
            for item in facilities:
                item["source_url"] = url
                item["scraped_at"] = utc_now_iso()
            return facilities, errors
        except Exception as exc:
            errors.append({"source": "facility_hours", "url": url, "error": str(exc)})
            sleep_random(args.delay_min, args.delay_max)
    return [], errors


def importance_for_title(title: str) -> tuple[str, list[str]]:
    matched = [keyword for keyword in IMPORTANT_KEYWORDS if keyword in title]
    critical = [keyword for keyword in CRITICAL_KEYWORDS if keyword in title]
    if critical:
        return "high", list(dict.fromkeys(critical + matched))
    if matched:
        return "medium", matched
    return "low", []


def parse_news_list(html_text: str, base_url: str, *, category: str, max_news: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html_text, "html.parser")
    news: list[dict[str, Any]] = []
    seen_links: set[str] = set()

    for anchor in soup.select("a[href*='/p/406-1052-']"):
        title = clean_text(anchor.get_text(" ", strip=True))
        if not title or title in {"1", "2", "3", "4", "5", "6", ">", ">>"}:
            continue
        href = urljoin(base_url, str(anchor.get("href")))
        if href in seen_links:
            continue
        seen_links.add(href)

        date_text = find_nearby_date(anchor)
        importance, keywords = importance_for_title(title)
        if not keywords:
            continue
        news.append(
            {
                "title": title,
                "published_date": date_text,
                "url": href,
                "category": category,
                "importance": importance,
                "matched_keywords": keywords,
                "source_url": base_url,
                "scraped_at": utc_now_iso(),
            }
        )
        if max_news and len(news) >= max_news:
            break
    return news


def find_nearby_date(anchor: Any) -> str | None:
    date_pattern = re.compile(r"20\d{2}-\d{2}-\d{2}")
    parent = anchor.parent
    for _ in range(4):
        if parent is None:
            break
        text = clean_text(parent.get_text("\n", strip=True))
        match = date_pattern.search(text)
        if match:
            return match.group(0)
        parent = parent.parent

    previous_text = ""
    for sibling in anchor.find_all_previous(string=True, limit=8):
        previous_text = f"{sibling}\n{previous_text}"
        match = date_pattern.search(previous_text)
        if match:
            return match.group(0)
    return None


def scrape_dorm_news(session: requests.Session, args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    all_news: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for url in DORM_NEWS_URLS:
        try:
            html_text = fetch_html(
                session,
                url,
                timeout=args.timeout,
                retries=args.retries,
                delay_min=args.delay_min,
                delay_max=args.delay_max,
            )
            category = "宿舍活動公告" if "407" in url else "住服組公告"
            all_news.extend(parse_news_list(html_text, url, category=category, max_news=args.max_news))
        except Exception as exc:
            errors.append({"source": "dorm_news", "url": url, "error": str(exc)})
        sleep_random(args.delay_min, args.delay_max)

    deduped: dict[str, dict[str, Any]] = {}
    for item in all_news:
        deduped.setdefault(item["url"], item)
    news = list(deduped.values())
    news.sort(key=lambda item: (item.get("importance") == "high", item.get("published_date") or ""), reverse=True)
    if args.max_news:
        news = news[: args.max_news]
    return news, errors


def scrape_campus_life(args: argparse.Namespace) -> dict[str, Any]:
    session = create_session()
    facilities, facility_errors = scrape_facility_hours(session, args)
    dorm_news, dorm_errors = scrape_dorm_news(session, args)
    return {
        "source": "campus_life",
        "generated_at": utc_now_iso(),
        "facility_hours": facilities,
        "dormitory_news": dorm_news,
        "errors": facility_errors + dorm_errors,
    }


def write_json(payload: dict[str, Any], output: str) -> None:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LOGGER.info("Wrote campus life updates to %s", path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--max-news", type=int, default=30)
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
        payload = scrape_campus_life(args)
        write_json(payload, args.output)
        print(
            "Campus life scrape summary: "
            f"facilities={len(payload['facility_hours'])}, "
            f"dorm_news={len(payload['dormitory_news'])}, "
            f"errors={len(payload['errors'])}"
        )
        return 0
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user.")
        return 130
    except Exception as exc:
        LOGGER.error("Campus life scrape failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
