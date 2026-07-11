"""
Async activity event pipeline for NCKU Hub.

This script simulates fetching large campus events such as the NCKU Bike
Festival and club welcome events, then stores them in the Activity table.

Local test:
    cd backend
    set DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/nckuall
    py -3.11 scripts/activity_event_pipeline.py --mock --create-tables

Future real JSON or HTML source:
    py -3.11 scripts/activity_event_pipeline.py --source-url "https://example.edu/events.json"

The HTML parser accepts official news lists, table schedules, and common
event cards. Missing location/source/description fields are tolerated and
filled with safe defaults during upsert.

Expected JSON shape:
    {
      "items": [
        {
          "event_name": "成大單車節",
          "date": "2026-03-14T09:00:00+08:00",
          "location": "成功校區",
          "source_link": "https://..."
        }
      ]
    }
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from os import getenv
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models import Activity, ActivityType, Base  # noqa: E402
from app.realtime.notifications import (  # noqa: E402
    NotificationPayload,
    publish_notifications_via_redis,
)


DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/nckuall"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 Safari/605.1.15",
]

MOCK_EVENTS = {
    "items": [
        {
            "event_name": "成大單車節",
            "date": "2026-03-14T09:00:00+08:00",
            "location": "成功校區榕園與周邊展區",
            "source_link": "https://activity.ncku.example/bike-festival-2026",
        },
        {
            "event_name": "帆船社迎新體驗",
            "date": "2026-09-20T14:00:00+08:00",
            "location": "安平港與成大社團教室",
            "source_link": "https://activity.ncku.example/sailing-welcome-2026",
        },
        {
            "event_name": "成大社團博覽會",
            "date": "2026-09-12T10:00:00+08:00",
            "location": "光復校區雲平大樓前廣場",
            "source_link": "https://activity.ncku.example/club-fair-2026",
        },
    ]
}


@dataclass(frozen=True)
class EventPayload:
    event_name: str
    date: datetime
    location: str | None
    source_link: str | None
    description: str | None = None
    tags: list[str] | None = None


async def fetch_source(url: str, *, min_delay: float) -> str:
    await asyncio.sleep(min_delay + random.uniform(0, 0.8))
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(
            url,
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
            },
        )
        response.raise_for_status()
        return response.text


def safe_text(node: Any, default: str = "") -> str:
    try:
        if node is None:
            return default
        return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() or default
    except Exception:
        return default


def parse_event_date(value: str) -> datetime:
    value = value.strip().replace("/", "-")
    value = re.sub(r"\s+", " ", value)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y.%m.%d %H:%M",
        "%Y.%m.%d",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(value)


def parse_events_json(payload: dict[str, Any]) -> list[EventPayload]:
    return [
        EventPayload(
            event_name=str(item["event_name"]).strip(),
            date=parse_event_date(str(item["date"]).strip()),
            location=(str(item.get("location")).strip() if item.get("location") else None),
            source_link=(str(item.get("source_link")).strip() if item.get("source_link") else None),
            description=(str(item.get("description")).strip() if item.get("description") else None),
            tags=list(item.get("tags") or []),
        )
        for item in payload.get("items", [])
    ]


def normalize_event_header(value: str) -> str:
    value = re.sub(r"\s+", "", value).strip().lower()
    aliases = {
        "活動名稱": "event_name",
        "標題": "event_name",
        "名稱": "event_name",
        "event": "event_name",
        "eventname": "event_name",
        "title": "event_name",
        "日期": "date",
        "時間": "date",
        "活動時間": "date",
        "date": "date",
        "time": "date",
        "datetime": "date",
        "地點": "location",
        "活動地點": "location",
        "location": "location",
        "說明": "description",
        "簡介": "description",
        "內容": "description",
        "description": "description",
        "連結": "source_link",
        "報名連結": "source_link",
        "source": "source_link",
        "url": "source_link",
        "link": "source_link",
    }
    return aliases.get(value, value)


def infer_event_tags(name: str, description: str | None) -> list[str]:
    text = f"{name} {description or ''}"
    tags = ["校園活動"]
    rules = {
        "大型活動": ("單車節", "舞會", "博覽會", "售票"),
        "社團": ("社團", "迎新", "體驗"),
        "講座": ("講座", "論壇", "演講"),
        "招生": ("招生", "說明會"),
    }
    for tag, keywords in rules.items():
        if any(keyword in text for keyword in keywords):
            tags.append(tag)
    return list(dict.fromkeys(tags))


class NckuEventHtmlParser:
    event_selectors = (
        "table#events tr",
        "table.table tr",
        "table tr",
        ".event-item",
        ".news-item",
        ".activity-item",
        "article",
        "li",
    )

    def __init__(self, *, base_url: str | None = None) -> None:
        self.base_url = base_url

    def parse(self, html: str) -> list[EventPayload]:
        soup = BeautifulSoup(html, "html.parser")
        events: list[EventPayload] = []
        seen: set[tuple[str, str]] = set()

        for table in soup.select("table"):
            events.extend(self._parse_table(table, seen))

        if not events:
            for selector in self.event_selectors:
                for node in soup.select(selector):
                    payload = self._parse_freeform_node(node)
                    if not payload:
                        continue
                    key = (payload.event_name, payload.date.isoformat())
                    if key in seen:
                        continue
                    seen.add(key)
                    events.append(payload)

        return events

    def _parse_table(self, table: Any, seen: set[tuple[str, str]]) -> list[EventPayload]:
        rows = table.find_all("tr")
        if not rows:
            return []
        header_map = self._build_header_map(rows[0])
        parsed: list[EventPayload] = []
        for row in rows[1:] if header_map else rows:
            try:
                payload = self._parse_row(row, header_map)
                if not payload:
                    continue
                key = (payload.event_name, payload.date.isoformat())
                if key in seen:
                    continue
                seen.add(key)
                parsed.append(payload)
            except Exception as exc:
                print(f"Warning: skipped malformed event row: {exc}")
                continue
        return parsed

    def _build_header_map(self, row: Any) -> dict[int, str]:
        header_map: dict[int, str] = {}
        for index, cell in enumerate(row.find_all(["th", "td"])):
            key = normalize_event_header(safe_text(cell))
            if key in {"event_name", "date", "location", "source_link", "description"}:
                header_map[index] = key
        return header_map

    def _parse_row(self, row: Any, header_map: dict[int, str]) -> EventPayload | None:
        cells = row.find_all("td")
        if len(cells) < 2:
            return None
        values: dict[str, str] = {}
        source_link: str | None = None
        if header_map:
            for index, cell in enumerate(cells):
                key = header_map.get(index)
                if not key:
                    continue
                values[key] = safe_text(cell)
                if key == "source_link":
                    source_link = self._extract_link(cell) or values[key]
        else:
            text_cells = [safe_text(cell) for cell in cells]
            if len(text_cells) >= 2:
                values = {
                    "event_name": text_cells[0],
                    "date": text_cells[1],
                    "location": text_cells[2] if len(text_cells) >= 3 else "",
                    "description": " ".join(text_cells[3:]) if len(text_cells) >= 4 else "",
                }
                source_link = self._extract_link(row)
        return self._payload_from_values(values, source_link=source_link or self._extract_link(row))

    def _parse_freeform_node(self, node: Any) -> EventPayload | None:
        text = safe_text(node)
        date_match = re.search(r"(\d{4}[./-]\d{1,2}[./-]\d{1,2}(?:\s+\d{1,2}:\d{2})?)", text)
        if not date_match:
            return None
        title_node = node.find(["h1", "h2", "h3", "h4", "a"])
        event_name = safe_text(title_node) or text[:40]
        values = {
            "event_name": event_name,
            "date": date_match.group(1),
            "location": self._extract_labeled_text(text, ("地點", "location")),
            "description": text,
        }
        return self._payload_from_values(values, source_link=self._extract_link(node))

    def _payload_from_values(self, values: dict[str, str], *, source_link: str | None) -> EventPayload | None:
        event_name = values.get("event_name", "").strip()
        raw_date = values.get("date", "").strip()
        if not event_name or not raw_date:
            return None
        description = values.get("description") or None
        return EventPayload(
            event_name=event_name,
            date=parse_event_date(raw_date),
            location=values.get("location") or None,
            source_link=urljoin(self.base_url or "", source_link) if source_link else None,
            description=description,
            tags=infer_event_tags(event_name, description),
        )

    def _extract_link(self, node: Any) -> str | None:
        try:
            link = node.find("a", href=True)
            return urljoin(self.base_url or "", link["href"]) if link else None
        except Exception:
            return None

    @staticmethod
    def _extract_labeled_text(text: str, labels: tuple[str, ...]) -> str:
        for label in labels:
            match = re.search(rf"{label}[:：]\s*([^。；;，,\n]+)", text, re.I)
            if match:
                return match.group(1).strip()
        return ""


def parse_events_html(html: str, *, base_url: str | None = None) -> list[EventPayload]:
    """
    Parse NCKU-like event pages, official news lists, or table schedules.

    Supported columns include event_name/title, date/time, location,
    source_link/link, and description. Missing optional fields are tolerated.
    """

    return NckuEventHtmlParser(base_url=base_url).parse(html)


async def upsert_event(
    session,
    payload: EventPayload,
) -> tuple[Activity, bool]:
    existing = None
    if payload.source_link:
        existing = await session.scalar(select(Activity).where(Activity.official_url == payload.source_link))

    if existing is None:
        existing = await session.scalar(
            select(Activity).where(Activity.title == payload.event_name, Activity.start_at == payload.date)
        )

    if existing is None:
        activity = Activity(
            activity_type=ActivityType.OFFICIAL_EVENT,
            title=payload.event_name,
            organizer_name="NCKU",
            description=payload.description or f"{payload.event_name}活動時程匯入資料。",
            location=payload.location,
            start_at=payload.date,
            official_url=payload.source_link,
            tags=payload.tags or infer_event_tags(payload.event_name, payload.description),
            is_official=False,
        )
        session.add(activity)
        await session.flush()
        return activity, True

    existing.title = payload.event_name
    existing.location = payload.location
    existing.start_at = payload.date
    existing.official_url = payload.source_link
    existing.description = payload.description or existing.description
    existing.tags = payload.tags or existing.tags
    return existing, False


async def run(args: argparse.Namespace) -> None:
    if args.mock:
        events = parse_events_json(MOCK_EVENTS)
    else:
        if not args.source_url:
            raise ValueError("Use --mock or provide --source-url.")
        raw = await fetch_source(args.source_url, min_delay=args.min_delay)
        events = (
            parse_events_json(json.loads(raw))
            if raw.lstrip().startswith("{")
            else parse_events_html(raw, base_url=args.source_url)
        )

    engine = create_async_engine(getenv("DATABASE_URL", DEFAULT_DATABASE_URL), pool_pre_ping=True)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    created_events: list[Activity] = []

    try:
        if args.create_tables:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await conn.run_sync(Base.metadata.create_all)

        async with async_session() as session:
            async with session.begin():
                for event in events:
                    activity, was_created = await upsert_event(session, event)
                    if was_created:
                        created_events.append(activity)

        notifications = [
            NotificationPayload(
                kind="event.created",
                topic="all",
                title=f"新校園活動：{event.title}",
                summary=" · ".join(
                    value
                    for value in [
                        event.start_at.strftime("%Y/%m/%d %H:%M")
                        if event.start_at
                        else None,
                        event.location,
                    ]
                    if value
                )
                or "新的校園大型活動已發布。",
                href=f"/events#event-{event.id}",
                resource_id=str(event.id),
            )
            for event in created_events
        ]
        try:
            published = await publish_notifications_via_redis(
                notifications,
                redis_url=getenv("REDIS_URL"),
            )
        except Exception as exc:
            published = 0
            print(f"Warning: realtime notification publish failed: {exc}")

        print(
            "Activity pipeline complete: "
            f"events_upserted={len(events)} "
            f"events_created={len(created_events)} "
            f"notifications_published={published}"
        )
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch and upsert NCKU activity events.")
    parser.add_argument("--mock", action="store_true", help="Use built-in mock activity data.")
    parser.add_argument("--source-url", help="JSON or HTML schedule source URL.")
    parser.add_argument("--create-tables", action="store_true", help="Create tables before writing.")
    parser.add_argument("--min-delay", type=float, default=1.5, help="Polite crawling delay.")
    return parser


def main() -> None:
    asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
