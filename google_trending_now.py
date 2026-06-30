"""Collector for the Google Trends "Trending now" RSS feed.

The public Trending Now export is deliberately used instead of the Google
Trends API alpha.  The API alpha is intended for analysis and currently lags
roughly two days, whereas Trending Now is refreshed about every ten minutes.

RSS contains the freshest keyword, traffic tier, publication time, and news.
It does not expose the full UI's growth/status fields, so ``database.py``
derives growth from consecutive snapshots and marks a trend ended when it
disappears from a successful collection.
"""

from __future__ import annotations

import argparse
import email.utils
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import requests


RSS_URL = "https://trends.google.com/trending/rss"
HT = "{https://trends.google.com/trending/rss}"


class GoogleTrendingNowError(RuntimeError):
    """Raised when Trending Now cannot be downloaded or parsed."""


def parse_traffic(value: str | None) -> int:
    """Convert traffic tiers such as ``10K+`` or ``2만+`` to a lower bound."""

    if not value:
        return 0
    normalized = value.strip().upper().replace(",", "").replace("SEARCHES", "")
    korean_units = {"천": 1_000, "만": 10_000, "억": 100_000_000}
    for unit, multiplier in korean_units.items():
        match = re.search(rf"([\d.]+)\s*{unit}", normalized)
        if match:
            return int(float(match.group(1)) * multiplier)
    match = re.search(r"([\d.]+)\s*([KMB]?)", normalized)
    if not match:
        return 0
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    return int(float(match.group(1)) * multiplier[match.group(2)])


def _text(node: ET.Element, tag: str, default: str = "") -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None else default


def _parse_datetime(value: str) -> str:
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat()


class GoogleTrendingNowCollector:
    """Download a market's current Google Trending Now RSS export."""

    def __init__(self, geo: str = "KR", timeout: int = 20) -> None:
        self.geo = geo.upper()
        self.timeout = timeout

    def fetch(self) -> list[dict[str, Any]]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; KoreaTrendDashboard/1.0; "
                "+https://trends.google.com/)"
            ),
            "Accept": "application/rss+xml, application/xml, text/xml",
        }
        try:
            response = requests.get(
                RSS_URL,
                params={"geo": self.geo},
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise GoogleTrendingNowError(f"Google Trending Now 요청 실패: {exc}") from exc

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise GoogleTrendingNowError("Google RSS XML 파싱 실패") from exc

        records: list[dict[str, Any]] = []
        for item in root.findall("./channel/item"):
            keyword = _text(item, "title")
            if not keyword:
                continue
            traffic_label = _text(item, f"{HT}approx_traffic", "0+")
            news: list[dict[str, str]] = []
            for article in item.findall(f"{HT}news_item"):
                news.append(
                    {
                        "title": _text(article, f"{HT}news_item_title"),
                        "url": _text(article, f"{HT}news_item_url"),
                        "source": _text(article, f"{HT}news_item_source"),
                    }
                )
            published = _parse_datetime(_text(item, "pubDate"))
            records.append(
                {
                    "keyword": keyword,
                    "volume_label": traffic_label,
                    "volume_min": parse_traffic(traffic_label),
                    "growth_rate": None,
                    # RSS publication time is the best available start proxy.
                    "started_at": published,
                    "is_active": True,
                    "related_queries": [],
                    "related_news": [article for article in news if article["title"]],
                    "explore_url": (
                        "https://trends.google.com/trends/explore?geo="
                        f"{self.geo}&q={quote_plus(keyword)}"
                    ),
                    # The legacy KR source name is retained for in-place DB upgrades.
                    "source": (
                        "google_trending_now_rss"
                        if self.geo == "KR"
                        else f"google_trending_now_rss_{self.geo}"
                    ),
                    "geo": self.geo,
                }
            )
        if not records:
            raise GoogleTrendingNowError("Google RSS에서 트렌드를 찾지 못했습니다.")
        return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Google Trending Now into SQLite")
    parser.add_argument("--db", default="trends.db", help="SQLite database path")
    parser.add_argument("--geo", default="KR", help="Google Trends geography code")
    args = parser.parse_args()

    from database import init_db, log_collection_run, save_google_trends

    init_db(args.db)
    started = datetime.now(timezone.utc)
    try:
        rows = GoogleTrendingNowCollector(args.geo).fetch()
        save_google_trends(args.db, rows, collected_at=started, is_mock=False)
        log_collection_run(
            args.db, "google", "success", len(rows), started_at=started, geo=args.geo.upper()
        )
        print(f"Collected {len(rows)} Google Trending Now records.")
    except Exception as exc:
        log_collection_run(
            args.db, "google", "failed", 0, str(exc), started_at=started, geo=args.geo.upper()
        )
        raise


if __name__ == "__main__":
    main()
