"""Scrape public Dcard NCKU/course posts into JSON.

The script targets Dcard's public/semi-public v2 API endpoints. Dcard may
return Cloudflare challenge HTML or 403 responses depending on IP/reputation;
those cases are recorded in the output `errors` array instead of being parsed
as empty data.

Examples:
    python backend/scrapers/dcard_reviews.py --keywords "微積分,教授名字"
    python backend/scrapers/dcard_reviews.py --keywords-file data/dcard_keywords.txt --pages 3
    python backend/scrapers/dcard_reviews.py --boards ncku,course --output data/dcard_reviews.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import random
import sys
import time
from pathlib import Path
from typing import Any

import requests

LOGGER = logging.getLogger("dcard_reviews")

DCARD_BASE = "https://www.dcard.tw"
DEFAULT_BOARDS = ["ncku", "course"]
DEFAULT_OUTPUT = "data/dcard_reviews.json"
DEFAULT_DELAY_MIN = 1.5
DEFAULT_DELAY_MAX = 4.0
DEFAULT_LIMIT = 30
DEFAULT_PAGES = 2
DEFAULT_TIMEOUT = 20

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]


class DcardBlockedError(RuntimeError):
    """Raised when Dcard returns Cloudflare/challenge HTML instead of JSON."""


def utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "DNT": "1",
            "Origin": DCARD_BASE,
            "Referer": f"{DCARD_BASE}/f/ncku",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
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
    params: dict[str, Any] | None = None,
    referer: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 3,
    delay_min: float = DEFAULT_DELAY_MIN,
    delay_max: float = DEFAULT_DELAY_MAX,
) -> Any:
    headers = {"Referer": referer} if referer else None
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, params=params, headers=headers, timeout=timeout)
            content_type = response.headers.get("content-type", "")
            if response.status_code in {403, 429}:
                raise DcardBlockedError(
                    f"Dcard returned HTTP {response.status_code}; likely Cloudflare/rate-limit."
                )
            if response.status_code in {500, 502, 503, 504}:
                raise requests.HTTPError(f"HTTP {response.status_code}", response=response)
            response.raise_for_status()
            if "application/json" not in content_type and not response.text.lstrip().startswith(("[", "{")):
                raise DcardBlockedError(
                    f"Dcard returned non-JSON content-type={content_type!r}; likely challenge HTML."
                )
            return response.json()
        except (requests.RequestException, ValueError, DcardBlockedError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            wait_seconds = (2 ** (attempt - 1)) + random.uniform(delay_min, delay_max)
            LOGGER.warning(
                "Dcard request failed (%s/%s): %s; retrying in %.1fs",
                attempt,
                retries,
                exc,
                wait_seconds,
            )
            time.sleep(wait_seconds)

    raise RuntimeError(f"Failed Dcard request after {retries} attempts: {url}") from last_error


def parse_keywords(raw_keywords: str, keywords_file: str | None) -> list[str]:
    keywords = [item.strip() for item in raw_keywords.split(",") if item.strip()]
    if keywords_file:
        keywords.extend(
            line.strip()
            for line in Path(keywords_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    return list(dict.fromkeys(keywords))


def contains_keyword(post: dict[str, Any], keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack = "\n".join(
        str(post.get(key) or "")
        for key in ("title", "excerpt", "content", "school", "department")
    ).lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def post_url(post: dict[str, Any], fallback_board: str) -> str:
    post_id = post.get("id")
    forum_alias = post.get("forumAlias") or fallback_board
    return f"{DCARD_BASE}/f/{forum_alias}/p/{post_id}"


def normalize_post(
    post: dict[str, Any],
    *,
    board: str,
    matched_keyword: str | None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detail = detail or {}
    merged = {**post, **detail}
    post_id = merged.get("id")
    return {
        "source": "dcard",
        "board": merged.get("forumAlias") or board,
        "matched_keyword": matched_keyword,
        "post_id": post_id,
        "title": str(merged.get("title") or "").strip(),
        "content": str(merged.get("content") or post.get("excerpt") or "").strip(),
        "excerpt": str(merged.get("excerpt") or post.get("excerpt") or "")[:500],
        "created_at": merged.get("createdAt"),
        "updated_at": merged.get("updatedAt"),
        "like_count": int(merged.get("likeCount") or 0),
        "comment_count": int(merged.get("commentCount") or 0),
        "url": post_url(merged, board),
        "topics": merged.get("topics") if isinstance(merged.get("topics"), list) else [],
        "school": merged.get("school"),
        "department": merged.get("department"),
        "scraped_at": utc_now_iso(),
    }


def fetch_post_detail(
    session: requests.Session,
    post_id: Any,
    *,
    board: str,
    args: argparse.Namespace,
) -> dict[str, Any] | None:
    if not post_id:
        return None
    detail_url = f"{DCARD_BASE}/service/api/v2/posts/{post_id}"
    try:
        sleep_random(args.delay_min, args.delay_max)
        detail = request_json_with_retries(
            session,
            detail_url,
            referer=f"{DCARD_BASE}/f/{board}/p/{post_id}",
            timeout=args.timeout,
            retries=args.retries,
            delay_min=args.delay_min,
            delay_max=args.delay_max,
        )
        return detail if isinstance(detail, dict) else None
    except Exception as exc:
        LOGGER.warning("Failed to fetch Dcard post detail id=%s: %s", post_id, exc)
        return None


def fetch_search_posts(
    session: requests.Session,
    *,
    board: str,
    keyword: str,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    posts: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    before: int | None = None

    for page in range(1, args.pages + 1):
        params: dict[str, Any] = {
            "query": keyword,
            "forum": board,
            "limit": args.limit,
        }
        if before:
            params["before"] = before

        try:
            LOGGER.info("Dcard search board=%s keyword=%s page=%s", board, keyword, page)
            payload = request_json_with_retries(
                session,
                f"{DCARD_BASE}/service/api/v2/search/posts",
                params=params,
                referer=f"{DCARD_BASE}/search?query={keyword}",
                timeout=args.timeout,
                retries=args.retries,
                delay_min=args.delay_min,
                delay_max=args.delay_max,
            )
            if not isinstance(payload, list):
                raise ValueError(f"Unexpected search payload type: {type(payload).__name__}")
            if not payload:
                break
            posts.extend(payload)
            before = payload[-1].get("id")
            if not before:
                break
        except Exception as exc:
            errors.append({"board": board, "keyword": keyword, "mode": "search", "error": str(exc)})
            break
        sleep_random(args.delay_min, args.delay_max)

    return posts, errors


def fetch_forum_posts(
    session: requests.Session,
    *,
    board: str,
    keywords: list[str],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    posts: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    before: int | None = None

    for page in range(1, args.pages + 1):
        params: dict[str, Any] = {"limit": args.limit}
        if before:
            params["before"] = before
        try:
            LOGGER.info("Dcard forum board=%s page=%s", board, page)
            payload = request_json_with_retries(
                session,
                f"{DCARD_BASE}/service/api/v2/forums/{board}/posts",
                params=params,
                referer=f"{DCARD_BASE}/f/{board}",
                timeout=args.timeout,
                retries=args.retries,
                delay_min=args.delay_min,
                delay_max=args.delay_max,
            )
            if not isinstance(payload, list):
                raise ValueError(f"Unexpected forum payload type: {type(payload).__name__}")
            if not payload:
                break
            posts.extend(post for post in payload if contains_keyword(post, keywords))
            before = payload[-1].get("id")
            if not before:
                break
        except Exception as exc:
            errors.append({"board": board, "mode": "forum", "error": str(exc)})
            break
        sleep_random(args.delay_min, args.delay_max)

    return posts, errors


def scrape_dcard_reviews(args: argparse.Namespace) -> dict[str, Any]:
    boards = [board.strip() for board in args.boards.split(",") if board.strip()]
    keywords = parse_keywords(args.keywords, args.keywords_file)
    session = create_session()

    seen_ids: set[str] = set()
    reviews: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for board in boards:
        board_posts: list[tuple[dict[str, Any], str | None]] = []

        if keywords:
            for keyword in keywords:
                found, search_errors = fetch_search_posts(
                    session,
                    board=board,
                    keyword=keyword,
                    args=args,
                )
                errors.extend(search_errors)
                board_posts.extend((post, keyword) for post in found)

        if args.include_forum_fallback:
            found, forum_errors = fetch_forum_posts(
                session,
                board=board,
                keywords=keywords,
                args=args,
            )
            errors.extend(forum_errors)
            board_posts.extend((post, None) for post in found)

        for post, matched_keyword in board_posts:
            post_id = str(post.get("id") or "")
            if not post_id or post_id in seen_ids:
                continue
            seen_ids.add(post_id)
            if args.max_posts and len(reviews) >= args.max_posts:
                break
            detail = fetch_post_detail(session, post_id, board=board, args=args)
            normalized = normalize_post(
                post,
                board=board,
                matched_keyword=matched_keyword,
                detail=detail,
            )
            if contains_keyword(normalized, keywords):
                reviews.append(normalized)

    return {
        "source": "dcard",
        "generated_at": utc_now_iso(),
        "boards": boards,
        "keywords": keywords,
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
    LOGGER.info("Wrote Dcard reviews JSON to %s", output_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boards", default=",".join(DEFAULT_BOARDS), help="Comma-separated board aliases.")
    parser.add_argument("--keywords", default="", help="Comma-separated keywords, e.g. 微積分,教授名字")
    parser.add_argument("--keywords-file", default="", help="UTF-8 text file, one keyword per line.")
    parser.add_argument("--pages", type=int, default=DEFAULT_PAGES)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--max-posts", type=int, default=0, help="Testing guardrail; 0 means unlimited.")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--delay-min", type=float, default=DEFAULT_DELAY_MIN)
    parser.add_argument("--delay-max", type=float, default=DEFAULT_DELAY_MAX)
    parser.add_argument(
        "--include-forum-fallback",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Also crawl forum pages and filter keywords locally.",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if not args.keywords and not args.keywords_file:
        LOGGER.warning("No keywords provided; forum fallback will collect recent posts from target boards.")

    try:
        payload = scrape_dcard_reviews(args)
        write_json(payload, args.output)
        if payload["errors"]:
            LOGGER.warning("Completed with %s source error(s). See JSON errors field.", len(payload["errors"]))
        return 0
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user.")
        return 130
    except Exception as exc:
        LOGGER.error("Dcard scrape failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
