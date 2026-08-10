"""Search public PTT boards for NCKU/course review discussions and output JSON.

The script uses PTT's public HTML search pages. It sets the over18 cookie,
limits request rate, retries transient resets, and never logs in.

Examples:
    python backend/scrapers/ptt_course_reviews.py --keywords "資料結構,演算法" --boards NCKU,Course
    python backend/scrapers/ptt_course_reviews.py --keywords "王教授" --pages 1 --max-articles 5 --output ptt.json
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
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup, Tag

LOGGER = logging.getLogger("ptt_course_reviews")

PTT_BASE = "https://www.ptt.cc"
DEFAULT_BOARDS = ["NCKU", "Course"]
DEFAULT_TIMEOUT = 20
DEFAULT_DELAY_SECONDS = 1.0

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]


def utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(value)
    text = BeautifulSoup(text, "html.parser").get_text("\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def create_session() -> requests.Session:
    session = requests.Session()
    session.cookies.set("over18", "1", domain="www.ptt.cc")
    session.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
        }
    )
    return session


def request_with_retries(
    session: requests.Session,
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = 3,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            if response.status_code in {403, 429, 500, 502, 503, 504}:
                raise requests.HTTPError(f"HTTP {response.status_code} from {url}", response=response)
            response.raise_for_status()
            response.encoding = "utf-8"
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt == max_retries:
                break
            wait_seconds = (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            LOGGER.warning(
                "Request failed (%s/%s): %s; retrying in %.1fs",
                attempt,
                max_retries,
                exc,
                wait_seconds,
            )
            time.sleep(wait_seconds)
    raise RuntimeError(f"Failed request after {max_retries} attempts: {url}") from last_error


def parse_search_page(html_text: str, board: str) -> tuple[list[dict[str, str]], str | None]:
    soup = BeautifulSoup(html_text, "html.parser")
    posts: list[dict[str, str]] = []

    for entry in soup.select(".r-ent"):
        title_node = entry.select_one(".title a")
        if not title_node:
            continue
        href = str(title_node.get("href") or "").strip()
        if not href:
            continue
        posts.append(
            {
                "board": board,
                "title": clean_text(title_node.get_text(" ", strip=True)),
                "url": urljoin(PTT_BASE, href),
                "author": clean_text(entry.select_one(".author").get_text(" ", strip=True))
                if entry.select_one(".author")
                else "",
                "date": clean_text(entry.select_one(".date").get_text(" ", strip=True))
                if entry.select_one(".date")
                else "",
                "nrec": clean_text(entry.select_one(".nrec").get_text(" ", strip=True))
                if entry.select_one(".nrec")
                else "",
            }
        )

    prev_url = None
    for anchor in soup.select(".btn-group-paging a[href]"):
        if "上頁" in anchor.get_text(" ", strip=True):
            prev_url = urljoin(PTT_BASE, str(anchor.get("href")))
            break
    return posts, prev_url


def read_meta(main_content: Tag) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in main_content.select(".article-metaline, .article-metaline-right"):
        tag = line.select_one(".article-meta-tag")
        value = line.select_one(".article-meta-value")
        if tag and value:
            metadata[clean_text(tag.get_text(" ", strip=True))] = clean_text(
                value.get_text(" ", strip=True)
            )
    return metadata


def parse_pushes(main_content: Tag) -> list[dict[str, str]]:
    pushes: list[dict[str, str]] = []
    for push in main_content.select("div.push"):
        pushes.append(
            {
                "tag": clean_text(push.select_one(".push-tag").get_text(" ", strip=True))
                if push.select_one(".push-tag")
                else "",
                "user_id": clean_text(push.select_one(".push-userid").get_text(" ", strip=True))
                if push.select_one(".push-userid")
                else "",
                "content": clean_text(push.select_one(".push-content").get_text(" ", strip=True)).lstrip(": ")
                if push.select_one(".push-content")
                else "",
                "ipdatetime": clean_text(push.select_one(".push-ipdatetime").get_text(" ", strip=True))
                if push.select_one(".push-ipdatetime")
                else "",
            }
        )
    return pushes


def parse_article(html_text: str, url: str, *, include_comments: bool) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")
    main_content = soup.select_one("#main-content")
    if not main_content:
        return {"url": url, "content": "", "metadata": {}, "pushes": []}

    metadata = read_meta(main_content)
    pushes = parse_pushes(main_content) if include_comments else []

    content_clone = BeautifulSoup(str(main_content), "html.parser")
    content_main = content_clone.select_one("#main-content")
    if content_main:
        for unwanted in content_main.select(
            ".article-metaline, .article-metaline-right, div.push, span.f2, span.f6"
        ):
            unwanted.decompose()
        raw_content = content_main.get_text("\n", strip=True)
    else:
        raw_content = main_content.get_text("\n", strip=True)

    content = clean_text(raw_content)
    content = re.split(r"\n--(?:\s*\n|$)|※ 發信站:", content, maxsplit=1)[0].strip()
    return {
        "url": url,
        "content": content,
        "metadata": metadata,
        "pushes": pushes,
    }


def keyword_excerpt(content: str, keyword: str, radius: int = 90) -> str:
    if not content:
        return ""
    index = content.lower().find(keyword.lower())
    if index < 0:
        return content[: radius * 2].strip()
    start = max(index - radius, 0)
    end = min(index + len(keyword) + radius, len(content))
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(content) else ""
    return f"{prefix}{content[start:end].strip()}{suffix}"


def infer_review_tags(title: str, content: str) -> list[str]:
    text = f"{title}\n{content}"
    tag_rules = {
        "course_review": ["心得", "評價", "感想", "推薦", "雷", "甜", "涼", "硬"],
        "grade": ["成績", "分數", "調分", "當人", "A+", "不及格"],
        "teacher": ["教授", "老師", "授課"],
        "ncku": ["成大", "成功大學", "NCKU"],
    }
    return [tag for tag, words in tag_rules.items() if any(word in text for word in words)]


def scrape_ptt_reviews(args: argparse.Namespace) -> dict[str, Any]:
    keywords = [item.strip() for item in args.keywords.split(",") if item.strip()]
    if args.keywords_file:
        keywords.extend(
            item.strip()
            for item in Path(args.keywords_file).read_text(encoding="utf-8").splitlines()
            if item.strip() and not item.strip().startswith("#")
        )
    keywords = list(dict.fromkeys(keywords))
    boards = [item.strip() for item in args.boards.split(",") if item.strip()]
    if not keywords:
        raise ValueError("--keywords is required, e.g. --keywords '資料結構,王教授'")

    session = create_session()
    seen_urls: set[str] = set()
    reviews: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for board in boards:
        for keyword in keywords:
            next_url = f"{PTT_BASE}/bbs/{quote_plus(board)}/search?q={quote_plus(keyword)}"
            for page in range(1, args.pages + 1):
                if args.max_articles and len(reviews) >= args.max_articles:
                    break
                try:
                    LOGGER.info("Searching board=%s keyword=%s page=%s", board, keyword, page)
                    search_response = request_with_retries(session, next_url, timeout=args.timeout)
                    posts, prev_url = parse_search_page(search_response.text, board)
                except Exception as exc:
                    LOGGER.error("Search failed board=%s keyword=%s: %s", board, keyword, exc)
                    errors.append({"board": board, "keyword": keyword, "error": str(exc)})
                    break

                for post in posts:
                    if args.max_articles and len(reviews) >= args.max_articles:
                        break
                    if post["url"] in seen_urls:
                        continue
                    seen_urls.add(post["url"])
                    time.sleep(max(args.delay, 0))
                    try:
                        article_response = request_with_retries(
                            session, post["url"], timeout=args.timeout
                        )
                        article = parse_article(
                            article_response.text,
                            post["url"],
                            include_comments=args.include_comments,
                        )
                    except Exception as exc:
                        LOGGER.warning("Article fetch failed %s: %s", post["url"], exc)
                        errors.append({"url": post["url"], "error": str(exc)})
                        continue

                    content = article["content"]
                    reviews.append(
                        {
                            "source": "ptt",
                            "board": board,
                            "matched_keyword": keyword,
                            "title": post["title"],
                            "author": post["author"] or article["metadata"].get("作者", ""),
                            "posted_at_raw": article["metadata"].get("時間", post["date"]),
                            "url": post["url"],
                            "nrec": post["nrec"],
                            "content": content,
                            "excerpt": keyword_excerpt(content, keyword),
                            "tags": infer_review_tags(post["title"], content),
                            "pushes": article["pushes"],
                            "scraped_at": utc_now_iso(),
                        }
                    )

                if not prev_url:
                    break
                next_url = prev_url
                time.sleep(max(args.delay, 0))

    return {
        "source": "ptt",
        "generated_at": utc_now_iso(),
        "boards": boards,
        "keywords": keywords,
        "review_count": len(reviews),
        "reviews": reviews,
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
    parser.add_argument("--keywords", default="", help="Comma-separated search keywords.")
    parser.add_argument("--keywords-file", default="", help="UTF-8 file, one keyword per line.")
    parser.add_argument("--boards", default=",".join(DEFAULT_BOARDS), help="Comma-separated PTT boards.")
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--max-articles", type=int, default=0, help="Testing guardrail; 0 means unlimited.")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--include-comments", action="store_true", help="Include push comments in JSON.")
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
        payload = scrape_ptt_reviews(args)
        write_json(payload, args.output or None)
        return 0
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user.")
        return 130
    except Exception as exc:
        LOGGER.error("PTT scrape failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
