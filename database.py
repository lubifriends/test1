"""SQLite persistence and dashboard queries for multi-market trends."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


DEFAULT_DB_PATH = Path(__file__).with_name("trends.db")
SUPPORTED_GEOS = ("KR", "JP")


def _iso(value: datetime | str | None = None) -> str:
    if isinstance(value, str):
        return value
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


@contextmanager
def connect(db_path: str | Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """Create schema and migrate databases produced by the KR-only MVP."""

    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                volume_label TEXT NOT NULL,
                volume_min INTEGER NOT NULL DEFAULT 0,
                growth_rate REAL,
                started_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                ended_at TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                related_queries TEXT NOT NULL DEFAULT '[]',
                related_news TEXT NOT NULL DEFAULT '[]',
                explore_url TEXT,
                source TEXT NOT NULL,
                is_mock INTEGER NOT NULL DEFAULT 0,
                geo TEXT NOT NULL DEFAULT 'KR',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(keyword, source, is_mock)
            );

            CREATE TABLE IF NOT EXISTS trend_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trend_id INTEGER NOT NULL REFERENCES trends(id) ON DELETE CASCADE,
                collected_at TEXT NOT NULL,
                volume_min INTEGER NOT NULL DEFAULT 0,
                growth_rate REAL,
                is_active INTEGER NOT NULL,
                UNIQUE(trend_id, collected_at)
            );

            CREATE TABLE IF NOT EXISTS naver_age_trends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                age_code TEXT NOT NULL,
                age_label TEXT NOT NULL,
                period TEXT NOT NULL,
                ratio REAL NOT NULL,
                time_unit TEXT NOT NULL DEFAULT 'date',
                collected_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'naver_datalab',
                is_mock INTEGER NOT NULL DEFAULT 0,
                geo TEXT NOT NULL DEFAULT 'KR',
                UNIQUE(keyword, age_code, period, is_mock)
            );

            CREATE TABLE IF NOT EXISTS collection_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector TEXT NOT NULL,
                status TEXT NOT NULL,
                records_count INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                geo TEXT NOT NULL DEFAULT 'KR',
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS regional_interest (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                country_geo TEXT NOT NULL DEFAULT 'KR',
                region_code TEXT NOT NULL,
                region_name TEXT NOT NULL,
                period TEXT NOT NULL,
                ratio REAL NOT NULL,
                source TEXT NOT NULL,
                is_mock INTEGER NOT NULL DEFAULT 0,
                collected_at TEXT NOT NULL,
                UNIQUE(keyword, country_geo, region_code, period, is_mock)
            );

            CREATE TABLE IF NOT EXISTS translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_text TEXT NOT NULL,
                source_lang TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                provider TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_text, source_lang, target_lang)
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_trend_time
                ON trend_snapshots(trend_id, collected_at DESC);
            """
        )

        # Existing user databases are upgraded in place.
        _ensure_column(conn, "trends", "geo", "TEXT NOT NULL DEFAULT 'KR'")
        _ensure_column(conn, "naver_age_trends", "geo", "TEXT NOT NULL DEFAULT 'KR'")
        _ensure_column(conn, "collection_runs", "geo", "TEXT NOT NULL DEFAULT 'KR'")
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_trends_geo_mode_started
                ON trends(geo, is_mock, started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_naver_geo_keyword_age_period
                ON naver_age_trends(geo, is_mock, keyword, age_code, period);
            CREATE INDEX IF NOT EXISTS idx_region_geo_keyword
                ON regional_interest(country_geo, is_mock, keyword, period);
            """
        )


def save_google_trends(
    db_path: str | Path,
    records: list[dict[str, Any]],
    collected_at: datetime | str | None = None,
    is_mock: bool = False,
) -> None:
    """Upsert a successful collection and close missing trends in that market."""

    if not records:
        return
    collected = _iso(collected_at)
    source = records[0].get("source", "google_trending_now_rss")
    geo = records[0].get("geo", "KR").upper()
    seen: set[str] = set()

    with connect(db_path) as conn:
        for record in records:
            keyword = str(record["keyword"]).strip()
            if not keyword:
                continue
            seen.add(keyword)
            row_source = record.get("source", source)
            row_geo = record.get("geo", geo).upper()
            existing = conn.execute(
                "SELECT * FROM trends WHERE keyword=? AND source=? AND is_mock=?",
                (keyword, row_source, int(is_mock)),
            ).fetchone()

            growth = record.get("growth_rate")
            if growth is None and existing is not None:
                previous = int(existing["volume_min"] or 0)
                current = int(record.get("volume_min", 0) or 0)
                growth = round(((current - previous) / previous) * 100, 1) if previous else None

            started_at = existing["started_at"] if existing else _iso(record.get("started_at") or collected)
            values = (
                keyword,
                record.get("volume_label", "0+"),
                int(record.get("volume_min", 0) or 0),
                growth,
                started_at,
                collected,
                None if record.get("is_active", True) else collected,
                int(bool(record.get("is_active", True))),
                json.dumps(record.get("related_queries", []), ensure_ascii=False),
                json.dumps(record.get("related_news", []), ensure_ascii=False),
                record.get("explore_url", ""),
                row_source,
                int(is_mock),
                row_geo,
                collected,
                collected,
            )
            conn.execute(
                """
                INSERT INTO trends (
                    keyword, volume_label, volume_min, growth_rate, started_at,
                    last_seen_at, ended_at, is_active, related_queries,
                    related_news, explore_url, source, is_mock, geo, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(keyword, source, is_mock) DO UPDATE SET
                    volume_label=excluded.volume_label,
                    volume_min=excluded.volume_min,
                    growth_rate=excluded.growth_rate,
                    last_seen_at=excluded.last_seen_at,
                    ended_at=excluded.ended_at,
                    is_active=excluded.is_active,
                    related_queries=excluded.related_queries,
                    related_news=excluded.related_news,
                    explore_url=excluded.explore_url,
                    geo=excluded.geo,
                    updated_at=excluded.updated_at
                """,
                values,
            )
            trend_id = conn.execute(
                "SELECT id FROM trends WHERE keyword=? AND source=? AND is_mock=?",
                (keyword, row_source, int(is_mock)),
            ).fetchone()["id"]
            conn.execute(
                """
                INSERT OR IGNORE INTO trend_snapshots
                    (trend_id, collected_at, volume_min, growth_rate, is_active)
                VALUES (?, ?, ?, ?, ?)
                """,
                (trend_id, collected, int(record.get("volume_min", 0) or 0), growth,
                 int(bool(record.get("is_active", True)))),
            )

        if seen:
            placeholders = ",".join("?" for _ in seen)
            conn.execute(
                f"""
                UPDATE trends
                SET is_active=0, ended_at=?, updated_at=?
                WHERE source=? AND geo=? AND is_mock=? AND is_active=1
                  AND keyword NOT IN ({placeholders})
                """,
                (collected, collected, source, geo, int(is_mock), *sorted(seen)),
            )


def save_mock_history(db_path: str | Path, history: Iterable[dict[str, Any]]) -> None:
    with connect(db_path) as conn:
        for point in history:
            geo = point.get("geo", "KR")
            row = conn.execute(
                "SELECT id FROM trends WHERE keyword=? AND geo=? AND is_mock=1",
                (point["keyword"], geo),
            ).fetchone()
            if row is None:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO trend_snapshots
                    (trend_id, collected_at, volume_min, growth_rate, is_active)
                VALUES (?, ?, ?, ?, ?)
                """,
                (row["id"], _iso(point["collected_at"]), int(point["volume_min"]),
                 point.get("growth_rate"), int(bool(point.get("is_active", True)))),
            )


def save_naver_age_trends(
    db_path: str | Path,
    records: Iterable[dict[str, Any]],
    collected_at: datetime | str | None = None,
    is_mock: bool = False,
) -> None:
    collected = _iso(collected_at)
    rows = [row for row in records if row.get("keyword") and row.get("period")]
    with connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO naver_age_trends (
                keyword, age_code, age_label, period, ratio, time_unit,
                collected_at, source, is_mock, geo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(keyword, age_code, period, is_mock) DO UPDATE SET
                ratio=excluded.ratio,
                collected_at=excluded.collected_at,
                source=excluded.source,
                geo=excluded.geo
            """,
            [
                (row["keyword"], row["age_code"], row["age_label"], row["period"],
                 float(row["ratio"]), row.get("time_unit", "date"), collected,
                 row.get("source", "naver_datalab"), int(is_mock), row.get("geo", "KR"))
                for row in rows
            ],
        )


def save_regional_interest(
    db_path: str | Path,
    records: Iterable[dict[str, Any]],
    collected_at: datetime | str | None = None,
    is_mock: bool = False,
) -> None:
    collected = _iso(collected_at)
    rows = [row for row in records if row.get("keyword") and row.get("region_code")]
    with connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO regional_interest (
                keyword, country_geo, region_code, region_name, period, ratio,
                source, is_mock, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(keyword, country_geo, region_code, period, is_mock) DO UPDATE SET
                ratio=excluded.ratio, source=excluded.source, collected_at=excluded.collected_at
            """,
            [
                (row["keyword"], row.get("country_geo", "KR"), row["region_code"],
                 row["region_name"], row.get("period", "7d"), float(row["ratio"]),
                 row.get("source", "google_trends_region"), int(is_mock), collected)
                for row in rows
            ],
        )


def log_collection_run(
    db_path: str | Path,
    collector: str,
    status: str,
    records_count: int,
    message: str = "",
    started_at: datetime | str | None = None,
    geo: str = "KR",
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collection_runs
                (collector, status, records_count, message, geo, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (collector, status, records_count, message, geo, _iso(started_at), _iso()),
        )


def get_trends(
    db_path: str | Path,
    is_mock: bool,
    hours: int | None = None,
    geo: str | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = [int(is_mock)]
    where = "WHERE is_mock=?"
    if geo:
        where += " AND geo=?"
        params.append(geo.upper())
    if hours is not None:
        where += " AND started_at>=?"
        params.append(_iso(datetime.now(timezone.utc) - timedelta(hours=hours)))
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM trends {where} ORDER BY is_active DESC, volume_min DESC, growth_rate DESC",
            params,
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["related_queries"] = json.loads(item.get("related_queries") or "[]")
        item["related_news"] = json.loads(item.get("related_news") or "[]")
        result.append(item)
    return result


def get_snapshot_history(
    db_path: str | Path,
    keyword: str,
    is_mock: bool,
    days: int = 7,
    geo: str = "KR",
) -> list[dict[str, Any]]:
    cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=days))
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.collected_at, s.volume_min, s.growth_rate, s.is_active
            FROM trend_snapshots s JOIN trends t ON t.id=s.trend_id
            WHERE t.keyword=? AND t.geo=? AND t.is_mock=? AND s.collected_at>=?
            ORDER BY s.collected_at
            """,
            (keyword, geo, int(is_mock), cutoff),
        ).fetchall()
    return [dict(row) for row in rows]


def get_naver_history(
    db_path: str | Path,
    is_mock: bool,
    keyword: str | None = None,
    geo: str = "KR",
) -> list[dict[str, Any]]:
    params: list[Any] = [int(is_mock), geo]
    keyword_clause = ""
    if keyword:
        keyword_clause = " AND keyword=?"
        params.append(keyword)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT keyword, age_code, age_label, period, ratio, time_unit
            FROM naver_age_trends
            WHERE is_mock=? AND geo=? {keyword_clause}
            ORDER BY keyword, age_code, period
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def get_regional_interest(
    db_path: str | Path,
    is_mock: bool,
    keyword: str,
    country_geo: str = "KR",
) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT keyword, region_code, region_name, period, ratio, source
            FROM regional_interest
            WHERE is_mock=? AND country_geo=? AND keyword=?
            ORDER BY ratio DESC
            """,
            (int(is_mock), country_geo, keyword),
        ).fetchall()
    return [dict(row) for row in rows]


def get_last_run(
    db_path: str | Path,
    collector: str | None = None,
    geo: str | None = None,
) -> dict[str, Any] | None:
    clauses, params = [], []
    if collector:
        clauses.append("collector=?")
        params.append(collector)
    if geo:
        clauses.append("geo=?")
        params.append(geo)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect(db_path) as conn:
        row = conn.execute(
            f"SELECT * FROM collection_runs {where} ORDER BY id DESC LIMIT 1", params
        ).fetchone()
    return dict(row) if row else None


def get_translations(
    db_path: str | Path,
    texts: Iterable[str],
    source_lang: str = "ja",
    target_lang: str = "ko",
) -> dict[str, str]:
    values = list(dict.fromkeys(text for text in texts if text))
    if not values:
        return {}
    placeholders = ",".join("?" for _ in values)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT source_text, translated_text FROM translations
            WHERE source_lang=? AND target_lang=?
              AND source_text IN ({placeholders})
            """,
            (source_lang, target_lang, *values),
        ).fetchall()
    return {row["source_text"]: row["translated_text"] for row in rows}


def save_translations(
    db_path: str | Path,
    translations: dict[str, str],
    source_lang: str = "ja",
    target_lang: str = "ko",
    provider: str = "mymemory",
) -> None:
    if not translations:
        return
    updated = _iso()
    with connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO translations (
                source_text, source_lang, target_lang, translated_text,
                provider, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_text, source_lang, target_lang) DO UPDATE SET
                translated_text=excluded.translated_text,
                provider=excluded.provider,
                updated_at=excluded.updated_at
            """,
            [
                (source, source_lang, target_lang, translated, provider, updated)
                for source, translated in translations.items()
                if source and translated
            ],
        )


def has_data(db_path: str | Path, is_mock: bool, geo: str | None = None) -> bool:
    params: list[Any] = [int(is_mock)]
    geo_clause = ""
    if geo:
        geo_clause = " AND geo=?"
        params.append(geo)
    with connect(db_path) as conn:
        count = conn.execute(
            f"SELECT COUNT(*) AS n FROM trends WHERE is_mock=? {geo_clause}", params
        ).fetchone()["n"]
    return bool(count)


def ensure_mock_data(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    init_db(db_path)
    from mock_data import (
        build_google_history,
        build_google_trends,
        build_naver_history,
        build_regional_interest,
    )

    now = datetime.now(timezone.utc)
    for geo in SUPPORTED_GEOS:
        demo_rows = build_google_trends(now, geo)
        expected_keywords = {row["keyword"] for row in demo_rows}
        with connect(db_path) as conn:
            existing_keywords = {
                row["keyword"] for row in conn.execute(
                    "SELECT keyword FROM trends WHERE is_mock=1 AND geo=?", (geo,)
                )
            }
            # Mock rows are generated assets, so replacing an outdated mock
            # set is safe and keeps KR/JP comparison internally consistent.
            if existing_keywords and existing_keywords != expected_keywords:
                conn.execute("DELETE FROM trends WHERE is_mock=1 AND geo=?", (geo,))
        if not has_data(db_path, True, geo):
            save_google_trends(db_path, demo_rows, now, is_mock=True)
            save_mock_history(db_path, build_google_history(now, geo))

    naver_rows = build_naver_history(now)
    expected_naver = {row["keyword"] for row in naver_rows}
    with connect(db_path) as conn:
        existing_naver = {
            row["keyword"] for row in conn.execute(
                "SELECT DISTINCT keyword FROM naver_age_trends WHERE is_mock=1 AND geo='KR'"
            )
        }
        if existing_naver and existing_naver != expected_naver:
            conn.execute("DELETE FROM naver_age_trends WHERE is_mock=1 AND geo='KR'")
    if not get_naver_history(db_path, True, geo="KR"):
        save_naver_age_trends(db_path, naver_rows, now, is_mock=True)

    region_rows = build_regional_interest(now)
    expected_regions = {row["keyword"] for row in region_rows}
    with connect(db_path) as conn:
        existing_regions = {
            row["keyword"] for row in conn.execute(
                "SELECT DISTINCT keyword FROM regional_interest WHERE is_mock=1 AND country_geo='KR'"
            )
        }
        if existing_regions and existing_regions != expected_regions:
            conn.execute("DELETE FROM regional_interest WHERE is_mock=1 AND country_geo='KR'")
    probe = next(iter(expected_regions))
    if not get_regional_interest(db_path, True, probe, "KR"):
        save_regional_interest(db_path, region_rows, now, is_mock=True)
