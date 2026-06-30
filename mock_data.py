"""Deterministic, date-relative demo data for Korea and Japan."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus


MOCK_TRENDS: dict[str, list[tuple[str, str, int, float, float, bool]]] = {
    "KR": [
        ("AI 교과서", "100K+", 100_000, 920, 0.7, True),
        ("프로야구 순위", "50K+", 50_000, 680, 1.6, True),
        ("장마 시작", "50K+", 50_000, 540, 2.4, True),
        ("청년 지원금", "20K+", 20_000, 460, 3.2, True),
        ("신작 드라마", "20K+", 20_000, 380, 3.8, True),
        ("아이돌 컴백", "20K+", 20_000, 760, 5.3, True),
        ("iPhone", "10K+", 10_000, 330, 6.8, True),
        ("MLB", "10K+", 10_000, 280, 8.2, True),
        ("러닝화 추천", "5K+", 5_000, 220, 12.0, True),
        ("항공권 특가", "5K+", 5_000, 180, 16.0, True),
        ("자격증 시험", "2K+", 2_000, 140, 19.0, False),
        ("여름 축제", "2K+", 2_000, 110, 22.0, False),
    ],
    "JP": [
        ("猛暑日", "100K+", 100_000, 880, 0.9, True),
        ("プロ野球速報", "50K+", 50_000, 710, 1.4, True),
        ("台風情報", "50K+", 50_000, 590, 2.1, True),
        ("新作アニメ", "20K+", 20_000, 520, 3.0, True),
        ("花火大会", "20K+", 20_000, 450, 4.1, True),
        ("推し活", "20K+", 20_000, 390, 5.6, True),
        ("iPhone", "10K+", 10_000, 360, 7.0, True),
        ("MLB", "10K+", 10_000, 300, 9.4, True),
        ("コンビニ新商品", "5K+", 5_000, 250, 12.5, True),
        ("旅行セール", "5K+", 5_000, 190, 15.0, True),
        ("資格試験", "2K+", 2_000, 130, 19.5, False),
        ("夏フェス", "2K+", 2_000, 105, 22.5, False),
    ],
}

GEO_META = {
    "KR": {"hl": "ko", "gl": "KR", "ceid": "KR:ko", "source": "mock_google_trending_now"},
    "JP": {"hl": "ja", "gl": "JP", "ceid": "JP:ja", "source": "mock_google_trending_now_JP"},
}


def build_google_trends(
    now: datetime | None = None,
    geo: str = "KR",
) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    geo = geo.upper()
    meta = GEO_META[geo]
    rows = []
    for keyword, label, volume, growth, hours_ago, active in MOCK_TRENDS[geo]:
        search_url = (
            f"https://news.google.com/search?q={quote_plus(keyword)}"
            f"&hl={meta['hl']}&gl={meta['gl']}&ceid={meta['ceid']}"
        )
        rows.append(
            {
                "keyword": keyword,
                "volume_label": label,
                "volume_min": volume,
                "growth_rate": growth,
                "started_at": (now - timedelta(hours=hours_ago)).isoformat(),
                "is_active": active,
                "related_queries": [f"{keyword} 최신", f"{keyword} 일정"],
                "related_news": [{
                    "title": f"[샘플] {keyword} 관련 관심 급증 배경",
                    "url": search_url,
                    "source": "Demo news",
                }],
                "explore_url": (
                    f"https://trends.google.com/trends/explore?geo={geo}&q={quote_plus(keyword)}"
                ),
                "source": meta["source"],
                "geo": geo,
            }
        )
    return rows


def _factor(keyword: str, offset: int) -> float:
    digest = hashlib.sha256(f"{keyword}:{offset}".encode("utf-8")).digest()
    return 0.78 + digest[0] / 255 * 0.38


def build_google_history(
    now: datetime | None = None,
    geo: str = "KR",
) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    points = []
    for keyword, _label, volume, growth, _hours_ago, active in MOCK_TRENDS[geo]:
        for day in range(6, -1, -1):
            ramp = 0.32 + (6 - day) * 0.11
            points.append(
                {
                    "keyword": keyword,
                    "geo": geo,
                    "collected_at": (now - timedelta(days=day)).replace(hour=3).isoformat(),
                    "volume_min": max(100, int(volume * ramp * _factor(keyword, day))),
                    "growth_rate": growth * ramp,
                    "is_active": active or day > 0,
                }
            )
    return points


def build_naver_history(now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    rows = []
    age_bias = [
        (1.22, 1.08, 0.94), (0.82, 1.02, 1.15), (0.78, 0.95, 1.08),
        (0.72, 1.28, 1.34), (1.04, 1.16, 1.08), (1.36, 1.24, 0.92),
        (1.18, 1.20, 1.05), (0.83, 1.04, 1.16), (0.88, 1.16, 1.22),
        (0.72, 1.08, 1.29), (0.84, 1.18, 1.23), (1.02, 1.14, 1.09),
    ]
    labels = {"2": "13~18세", "3": "19~24세", "4": "25~29세"}
    for keyword_index, (keyword, *_rest) in enumerate(MOCK_TRENDS["KR"]):
        biases = age_bias[keyword_index]
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
                        "geo": "KR",
                    }
                )
    return rows


def build_regional_interest(now: datetime | None = None) -> list[dict[str, Any]]:
    """Demo-only regional values; live UI links to official Google Explore."""

    now = now or datetime.now(timezone.utc)
    regions = [
        ("KR-11", "서울"), ("KR-41", "경기"), ("KR-26", "부산"),
        ("KR-28", "인천"), ("KR-27", "대구"), ("KR-30", "대전"),
        ("KR-29", "광주"), ("KR-31", "울산"), ("KR-50", "제주"),
    ]
    rows = []
    for keyword, *_rest in MOCK_TRENDS["KR"]:
        raw = [30 + int(_factor(keyword + code, index) * 55) for index, (code, _name) in enumerate(regions)]
        maximum = max(raw)
        for (code, name), value in zip(regions, raw):
            rows.append(
                {
                    "keyword": keyword,
                    "country_geo": "KR",
                    "region_code": code,
                    "region_name": name,
                    "period": "7d",
                    "ratio": round(value / maximum * 100, 1),
                    "source": "mock_google_region_interest",
                }
            )
    return rows
