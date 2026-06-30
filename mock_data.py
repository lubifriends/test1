"""Deterministic, date-relative demo data used when credentials are absent."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus


MOCK_TRENDS = [
    ("AI 교과서", "100K+", 100_000, 920, 0.7, True),
    ("프로야구 순위", "50K+", 50_000, 680, 1.6, True),
    ("장마 시작", "50K+", 50_000, 540, 2.4, True),
    ("청년 지원금", "20K+", 20_000, 460, 3.2, True),
    ("신작 드라마", "20K+", 20_000, 380, 3.8, True),
    ("아이돌 컴백", "20K+", 20_000, 760, 5.3, True),
    ("대학 축제", "10K+", 10_000, 310, 7.5, True),
    ("러닝화 추천", "10K+", 10_000, 260, 9.0, True),
    ("편의점 신상", "5K+", 5_000, 220, 12.0, True),
    ("항공권 특가", "5K+", 5_000, 180, 16.0, True),
    ("자격증 시험", "2K+", 2_000, 140, 19.0, False),
    ("여름 축제", "2K+", 2_000, 110, 22.0, False),
]


def build_google_trends(now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    rows = []
    for keyword, label, volume, growth, hours_ago, active in MOCK_TRENDS:
        search_url = f"https://news.google.com/search?q={quote_plus(keyword)}&hl=ko&gl=KR&ceid=KR%3Ako"
        rows.append(
            {
                "keyword": keyword,
                "volume_label": label,
                "volume_min": volume,
                "growth_rate": growth,
                "started_at": (now - timedelta(hours=hours_ago)).isoformat(),
                "is_active": active,
                "related_queries": [f"{keyword} 뜻", f"{keyword} 최신", f"{keyword} 일정"],
                "related_news": [
                    {
                        "title": f"[샘플] {keyword}, 오늘 관심 급증 배경은",
                        "url": search_url,
                        "source": "샘플 뉴스",
                    }
                ],
                "explore_url": f"https://trends.google.com/trends/explore?geo=KR&q={quote_plus(keyword)}",
                "source": "mock_google_trending_now",
            }
        )
    return rows


def _factor(keyword: str, offset: int) -> float:
    digest = hashlib.sha256(f"{keyword}:{offset}".encode("utf-8")).digest()
    return 0.78 + digest[0] / 255 * 0.38


def build_google_history(now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    points = []
    for keyword, _label, volume, growth, _hours_ago, active in MOCK_TRENDS:
        for day in range(6, -1, -1):
            ramp = 0.32 + (6 - day) * 0.11
            value = max(100, int(volume * ramp * _factor(keyword, day)))
            points.append(
                {
                    "keyword": keyword,
                    "collected_at": (now - timedelta(days=day)).replace(hour=3).isoformat(),
                    "volume_min": value,
                    "growth_rate": growth * ramp,
                    "is_active": active or day > 0,
                }
            )
    return points


def build_naver_history(now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    rows = []
    age_bias = {
        "AI 교과서": (1.22, 1.08, 0.94),
        "프로야구 순위": (0.82, 1.02, 1.15),
        "장마 시작": (0.78, 0.95, 1.08),
        "청년 지원금": (0.72, 1.28, 1.34),
        "신작 드라마": (1.04, 1.16, 1.08),
        "아이돌 컴백": (1.36, 1.24, 0.92),
        "대학 축제": (1.18, 1.38, 1.12),
        "러닝화 추천": (0.88, 1.16, 1.22),
        "편의점 신상": (1.31, 1.22, 0.94),
        "항공권 특가": (0.72, 1.08, 1.29),
        "자격증 시험": (0.84, 1.18, 1.23),
        "여름 축제": (1.02, 1.14, 1.09),
    }
    labels = {"2": "13~18세", "3": "19~24세", "4": "25~29세"}
    for keyword, *_ in MOCK_TRENDS:
        biases = age_bias[keyword]
        for code_index, age_code in enumerate(("2", "3", "4")):
            for day in range(6, -1, -1):
                base = 35 + (6 - day) * 7.5
                ratio = min(100, base * biases[code_index] * _factor(keyword + age_code, day))
                rows.append(
                    {
                        "keyword": keyword,
                        "age_code": age_code,
                        "age_label": labels[age_code],
                        "period": (now.date() - timedelta(days=day)).isoformat(),
                        "ratio": round(ratio, 2),
                        "time_unit": "date",
                        "source": "mock_naver_datalab",
                    }
                )
    return rows
