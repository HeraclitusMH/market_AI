"""Fetch and deduplicate RSS articles for the Claude Routine.

This script is infrastructure for the external Claude Code Routine. The trading
bot does not import or call it.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import requests

try:
    import feedparser  # type: ignore
except Exception:  # pragma: no cover - exercised only when dependency absent
    feedparser = None


RSS_FEEDS = [
    {"url": "https://feeds.reuters.com/reuters/businessNews", "label": "reuters_business"},
    {"url": "https://feeds.reuters.com/reuters/marketsNews", "label": "reuters_markets"},
    {"url": "https://www.cnbc.com/id/100003114/device/rss/rss.html", "label": "cnbc"},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US", "label": "yahoo_finance"},
    {"url": "https://seekingalpha.com/market_currents.xml", "label": "seeking_alpha"},
    {"url": "https://www.investing.com/rss/news.rss", "label": "investing_com"},
]

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
SEEN_PATH = DATA_DIR / "seen_articles.json"
PENDING_PATH = DATA_DIR / "_pending_analysis.json"
DEFAULT_TTL_HOURS = 48
ARTICLE_WINDOW_HOURS = 12
REQUEST_TIMEOUT_SECONDS = 15
USER_AGENT = "MarketAI-ClaudeRoutine/1.0"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = parsedate_to_datetime(text)
            except (TypeError, ValueError):
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def article_id(url: str) -> str:
    return hashlib.sha256(url.lower().strip().encode("utf-8")).hexdigest()[:8]


def clean_text(value: Any, *, max_len: Optional[int] = None) -> str:
    text = " ".join(str(value or "").split())
    if max_len is not None and len(text) > max_len:
        return text[:max_len].rstrip()
    return text


def load_seen() -> dict:
    if not SEEN_PATH.exists():
        return {
            "schema_version": 1,
            "last_cleanup": None,
            "ttl_hours": DEFAULT_TTL_HOURS,
            "stats": {
                "total_processed_lifetime": 0,
                "current_entries": 0,
            },
            "articles": {},
        }
    with SEEN_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError(f"Unsupported seen article cache schema in {SEEN_PATH}")
    data.setdefault("ttl_hours", DEFAULT_TTL_HOURS)
    data.setdefault("stats", {})
    data["stats"].setdefault("total_processed_lifetime", 0)
    data["stats"].setdefault("current_entries", 0)
    data.setdefault("articles", {})
    return data


def prune_seen(seen: dict, now: datetime) -> None:
    ttl_hours = int(seen.get("ttl_hours") or DEFAULT_TTL_HOURS)
    cutoff = now - timedelta(hours=ttl_hours)
    articles = seen.get("articles") or {}
    kept = {}
    for key, item in articles.items():
        seen_at = parse_dt((item or {}).get("seen_at"))
        if seen_at is None or seen_at >= cutoff:
            kept[key] = item
    seen["articles"] = kept
    seen["last_cleanup"] = iso_z(now)
    seen["stats"]["current_entries"] = len(kept)


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def fetch_feed(feed: dict) -> Tuple[List[dict], Optional[str]]:
    url = feed["url"]
    label = feed["label"]
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except Exception as exc:
        return [], f"{label}: fetch failed: {exc}"

    try:
        if feedparser is not None:
            return parse_with_feedparser(response.content, label), None
        return parse_with_elementtree(response.content, label), None
    except Exception as exc:
        return [], f"{label}: parse failed: {exc}"


def parse_with_feedparser(content: bytes, label: str) -> List[dict]:
    parsed = feedparser.parse(content)
    if getattr(parsed, "bozo", False) and not getattr(parsed, "entries", None):
        raise ValueError(getattr(parsed, "bozo_exception", "invalid feed"))
    articles = []
    for entry in parsed.entries:
        url = clean_text(entry.get("link"))
        if not url:
            continue
        published = (
            parse_dt(entry.get("published"))
            or parse_dt(entry.get("updated"))
            or _parse_struct_time(entry.get("published_parsed"))
            or _parse_struct_time(entry.get("updated_parsed"))
        )
        articles.append({
            "url": url,
            "title": clean_text(entry.get("title")),
            "summary": clean_text(entry.get("summary") or entry.get("description"), max_len=500),
            "feed": label,
            "published_at": published,
        })
    return articles


def _parse_struct_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime(*value[:6], tzinfo=timezone.utc)
    except Exception:
        return None


def parse_with_elementtree(content: bytes, label: str) -> List[dict]:
    root = ET.fromstring(content)
    items = list(root.findall(".//item"))
    if not items:
        items = list(root.findall(".//{http://www.w3.org/2005/Atom}entry"))
    articles = []
    for item in items:
        url = clean_text(_first_text(item, ["link", "{http://www.w3.org/2005/Atom}link"]))
        if not url:
            url = clean_text(_atom_link_href(item))
        if not url:
            continue
        published = parse_dt(_first_text(item, ["pubDate", "published", "updated", "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated"]))
        articles.append({
            "url": url,
            "title": clean_text(_first_text(item, ["title", "{http://www.w3.org/2005/Atom}title"])),
            "summary": clean_text(_first_text(item, ["description", "summary", "{http://www.w3.org/2005/Atom}summary", "{http://www.w3.org/2005/Atom}content"]), max_len=500),
            "feed": label,
            "published_at": published,
        })
    return articles


def _first_text(item: ET.Element, names: Iterable[str]) -> str:
    for name in names:
        found = item.find(name)
        if found is not None and found.text:
            return found.text
    return ""


def _atom_link_href(item: ET.Element) -> str:
    for link in item.findall("{http://www.w3.org/2005/Atom}link"):
        href = link.attrib.get("href")
        if href:
            return href
    return ""


def is_recent(article: dict, now: datetime) -> bool:
    published = article.get("published_at")
    if not isinstance(published, datetime):
        return False
    return now - timedelta(hours=ARTICLE_WINDOW_HOURS) <= published <= now + timedelta(minutes=5)


def normalize_url(url: str) -> str:
    url = clean_text(url)
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return url
    return ""


def build_pending_and_update_seen(seen: dict, fetched: List[dict], now: datetime) -> Tuple[List[dict], int]:
    known = seen.get("articles") or {}
    new_articles: List[dict] = []
    skipped_dedup = 0

    for article in fetched:
        url = normalize_url(article.get("url", ""))
        if not url:
            continue
        key = article_id(url)
        if key in known:
            skipped_dedup += 1
            continue

        published = article.get("published_at")
        pending = {
            "id": key,
            "url": url,
            "title": clean_text(article.get("title")),
            "summary": clean_text(article.get("summary"), max_len=500),
            "feed": clean_text(article.get("feed")),
            "published_at": iso_z(published if isinstance(published, datetime) else now),
        }
        new_articles.append(pending)
        known[key] = {
            "url": pending["url"],
            "title": pending["title"],
            "feed": pending["feed"],
            "published_at": pending["published_at"],
            "seen_at": iso_z(now),
        }

    seen["articles"] = known
    seen["stats"]["total_processed_lifetime"] = int(seen["stats"].get("total_processed_lifetime") or 0) + len(new_articles)
    seen["stats"]["current_entries"] = len(known)
    return new_articles, skipped_dedup


def main() -> int:
    now = utcnow()
    feeds_failed: List[str] = []
    try:
        seen = load_seen()
        prune_seen(seen, now)

        fetched: List[dict] = []
        total_fetched = 0
        for feed in RSS_FEEDS:
            articles, error = fetch_feed(feed)
            if error:
                feeds_failed.append(error)
                continue
            total_fetched += len(articles)
            fetched.extend(article for article in articles if is_recent(article, now))

        new_articles, skipped_dedup = build_pending_and_update_seen(seen, fetched, now)
        pending = {
            "generated_at": iso_z(now),
            "total_fetched": total_fetched,
            "skipped_dedup": skipped_dedup,
            "new_count": len(new_articles),
            "feeds_failed": feeds_failed,
            "articles": new_articles,
        }

        write_json_atomic(PENDING_PATH, pending)
        write_json_atomic(SEEN_PATH, seen)

        print(json.dumps({
            "status": "success" if new_articles else "no_new_articles",
            "new_count": len(new_articles),
            "total_fetched": total_fetched,
            "skipped_dedup": skipped_dedup,
            "feeds_failed": feeds_failed,
            "pending_path": str(PENDING_PATH),
        }, indent=2))
        if feeds_failed:
            return 1
        return 0 if new_articles else 2
    except Exception as exc:
        print(f"routine fetch failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
