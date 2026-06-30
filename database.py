"""SQLite persistence and dashboard queries."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


DEFAULT_DB_PATH = Path(__file__).with_name("trends.db")


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


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
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
                UNIQUE(keyword, age_code, period, is_mock)
            );

            CREATE TABLE IF NOT EXISTS collection_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector TEXT NOT NULL,
                status TEXT NOT NULL,
                records_count INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_trends_mode_started
                ON trends(is_mock, started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_snapshots_trend_time
                ON trend_snapshots(trend_id, collected_at DESC);
            CREATE INDEX IF NOT EXISTS idx_naver_keyword_age_period
                ON naver_age_trends(is_mock, keyword, age_code, period);
            """
        )


def save_google_trends(
    db_path: str | Path,
    records: list[dict[str, Any]],
    collected_at: datetime | str | None = None,
    is_mock: bool = False,
) -> None:
    """Upsert a successful collection and close trends missing from it."""

    if not records:
        return
    collected = _iso(collected_at)
    source = records[0].get("source", "google_trending_now_rss")
    seen: set[str] = set()

    with connect(db_path) as conn:
        for record in records:
            keyword = str(record["keyword"]).strip()
            if not keyword:
                continue
            seen.add(keyword)
            existing = conn.execute(
                "SELECT * FROM trends WHERE keyword=? AND source=? AND is_mock=?",
                (keyword, record.get("source", source), int(is_mock)),
            ).fetchone()

            growth = record.get("growth_rate")
            if growth is None and existing is not None:
                previous = int(existing["volume_min"] or 0)
                current = int(record.get("volume_min", 0) or 0)
                growth = round(((current - previous) / previous) * 100, 1) if previous else None

            started_at = (
                existing["started_at"]
                if existing is not None
                else _iso(record.get("started_at") or collected)
            )
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
                record.get("source", source),
                int(is_mock),
                collected,
                collected,
            )
            conn.execute(
                """
                INSERT INTO trends (
                    keyword, volume_label, volume_min, growth_rate, started_at,
                    last_seen_at, ended_at, is_active, related_queries,
                    related_news, explore_url, source, is_mock, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    updated_at=excluded.updated_at
                """,
                values,
            )
            trend_id = conn.execute(
                "SELECT id FROM trends WHERE keyword=? AND source=? AND is_mock=?",
                (keyword, record.get("source", source), int(is_mock)),
            ).fetchone()["id"]
            conn.execute(
                """
                INSERT OR IGNORE INTO trend_snapshots
                    (trend_id, collected_at, volume_min, growth_rate, is_active)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    trend_id,
                    collected,
                    int(record.get("volume_min", 0) or 0),
                    growth,
                    int(bool(record.get("is_active", True))),
                ),
            )

        # A missing keyword is considered ended only after a non-empty,
        # successful collection. This prevents transient network failures from
        # closing every trend.
        placeholders = ",".join("?" for _ in seen)
        if seen:
            conn.execute(
                f"""
                UPDATE trends
                SET is_active=0, ended_at=?, updated_at=?
                WHERE source=? AND is_mock=? AND is_active=1
                  AND keyword NOT IN ({placeholders})
                """,
                (collected, collected, source, int(is_mock), *sorted(seen)),
            )


def save_mock_history(
    db_path: str | Path,
    history: Iterable[dict[str, Any]],
) -> None:
    with connect(db_path) as conn:
        for point in history:
            row = conn.execute(
                "SELECT id FROM trends WHERE keyword=? AND is_mock=1",
                (point["keyword"],),
            ).fetchone()
            if row is None:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO trend_snapshots
                    (trend_id, collected_at, volume_min, growth_rate, is_active)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    _iso(point["collected_at"]),
                    int(point["volume_min"]),
                    point.get("growth_rate"),
                    int(bool(point.get("is_active", True))),
                ),
            )


def save_naver_age_trends(
    db_path: str | Path,
    records: Iterable[dict[str, Any]],
    collected_at: datetime | str | None = None,
    is_mock: bool = False,
) -> None:
    collected = _iso(collected_at)
    with connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO naver_age_trends (
                keyword, age_code, age_label, period, ratio, time_unit,
                collected_at, source, is_mock
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(keyword, age_code, period, is_mock) DO UPDATE SET
                ratio=excluded.ratio,
                collected_at=excluded.collected_at,
                source=excluded.source
            """,
            [
                (
                    row["keyword"],
                    row["age_code"],
                    row["age_label"],
                    row["period"],
                    float(row["ratio"]),
                    row.get("time_unit", "date"),
                    collected,
                    row.get("source", "naver_datalab"),
                    int(is_mock),
                )
                for row in records
                if row.get("keyword") and row.get("period")
            ],
        )


def log_collection_run(
    db_path: str | Path,
    collector: str,
    status: str,
    records_count: int,
    message: str = "",
    started_at: datetime | str | None = None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collection_runs
                (collector, status, records_count, message, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (collector, status, records_count, message, _iso(started_at), _iso()),
        )


def get_trends(
    db_path: str | Path,
    is_mock: bool,
    hours: int | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = [int(is_mock)]
    where = "WHERE is_mock=?"
    if hours is not None:
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(hours=hours))
        where += " AND started_at >= ?"
        params.append(cutoff)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM trends {where}
            ORDER BY is_active DESC, volume_min DESC, growth_rate DESC
            """,
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
) -> list[dict[str, Any]]:
    cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=days))
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.collected_at, s.volume_min, s.growth_rate, s.is_active
            FROM trend_snapshots s
            JOIN trends t ON t.id=s.trend_id
            WHERE t.keyword=? AND t.is_mock=? AND s.collected_at>=?
            ORDER BY s.collected_at
            """,
            (keyword, int(is_mock), cutoff),
        ).fetchall()
    return [dict(row) for row in rows]


def get_naver_history(
    db_path: str | Path,
    is_mock: bool,
    keyword: str | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = [int(is_mock)]
    keyword_clause = ""
    if keyword:
        keyword_clause = " AND keyword=?"
        params.append(keyword)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT keyword, age_code, age_label, period, ratio, time_unit
            FROM naver_age_trends
            WHERE is_mock=? {keyword_clause}
            ORDER BY keyword, age_code, period
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def get_last_run(db_path: str | Path, collector: str | None = None) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        if collector:
            row = conn.execute(
                "SELECT * FROM collection_runs WHERE collector=? ORDER BY id DESC LIMIT 1",
                (collector,),
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM collection_runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def has_data(db_path: str | Path, is_mock: bool) -> bool:
    with connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM trends WHERE is_mock=?", (int(is_mock),)
        ).fetchone()["n"]
    return bool(count)


def ensure_mock_data(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    init_db(db_path)
    if has_data(db_path, True):
        return
    from mock_data import build_google_history, build_google_trends, build_naver_history

    now = datetime.now(timezone.utc)
    save_google_trends(db_path, build_google_trends(now), collected_at=now, is_mock=True)
    save_mock_history(db_path, build_google_history(now))
    save_naver_age_trends(db_path, build_naver_history(now), collected_at=now, is_mock=True)

