"""Transparent scoring helpers for trend and age-fit rankings."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def trend_score(trend: dict[str, Any], now: datetime | None = None) -> float:
    """Blend volume, rise, recency, and active status into a 0-100 score."""

    now = now or datetime.now(timezone.utc)
    volume = max(float(trend.get("volume_min") or 0), 1)
    volume_component = min(math.log10(volume + 1) / 6 * 100, 100)
    growth = max(float(trend.get("growth_rate") or 0), 0)
    growth_component = min(growth / 10, 100)
    age_hours = max((now - _parse_time(trend["started_at"])).total_seconds() / 3600, 0)
    recency_component = max(0, 100 * (1 - age_hours / 24))
    active_component = 100 if trend.get("is_active") else 20
    score = (
        0.40 * volume_component
        + 0.25 * growth_component
        + 0.25 * recency_component
        + 0.10 * active_component
    )
    return round(min(max(score, 0), 100), 1)


def add_trend_scores(trends: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = [{**trend, "trend_score": trend_score(trend)} for trend in trends]
    return sorted(scored, key=lambda item: item["trend_score"], reverse=True)


def _series_score(points: list[dict[str, Any]]) -> tuple[float, float]:
    ordered = sorted(points, key=lambda point: point["period"])
    ratios = [float(point["ratio"]) for point in ordered]
    if not ratios:
        return 0.0, 0.0
    recent = sum(ratios[-2:]) / min(len(ratios), 2)
    baseline_points = ratios[:-2] or ratios
    baseline = sum(baseline_points) / len(baseline_points)
    momentum = ((recent - baseline) / baseline * 100) if baseline else 0.0
    # Ratio is already 0-100 within the Naver response. Momentum contributes
    # separately and is capped to avoid one noisy day dominating the score.
    naver_score = 0.7 * recent + 0.3 * min(max(50 + momentum / 2, 0), 100)
    return round(naver_score, 1), round(momentum, 1)


def rank_age_keywords(
    trends: Iterable[dict[str, Any]],
    naver_points: Iterable[dict[str, Any]],
    target: str,
) -> list[dict[str, Any]]:
    """Rank teen or twenties interest as an explicitly estimated proxy."""

    target_codes = {"teen": {"2"}, "twenties": {"3", "4"}}
    if target not in target_codes:
        raise ValueError("target must be 'teen' or 'twenties'")

    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for point in naver_points:
        if point["age_code"] in target_codes[target]:
            grouped[point["keyword"]][point["age_code"]].append(point)

    trend_map = {trend["keyword"]: trend for trend in trends}
    ranked = []
    for keyword, age_series in grouped.items():
        age_results = [_series_score(points) for points in age_series.values()]
        naver_score = sum(result[0] for result in age_results) / len(age_results)
        momentum = sum(result[1] for result in age_results) / len(age_results)
        google_score = float(trend_map.get(keyword, {}).get("trend_score", 0))
        estimate = 0.65 * naver_score + 0.35 * google_score
        ranked.append(
            {
                "keyword": keyword,
                "age_fit_score": round(estimate, 1),
                "naver_momentum": round(momentum, 1),
                "trend_score": round(google_score, 1),
            }
        )
    return sorted(ranked, key=lambda row: row["age_fit_score"], reverse=True)


def content_ideas(trends: Iterable[dict[str, Any]], limit: int = 6) -> list[dict[str, str]]:
    templates = [
        ("빠른 해설", "왜 지금 뜨나: 3분 핵심 정리"),
        ("숏폼", "60초로 보는 핵심 포인트 3가지"),
        ("검색형", "처음 보는 사람을 위한 뜻·배경·현재 상황"),
        ("비교형", "반응이 갈리는 이유와 체크할 사실"),
    ]
    ideas = []
    for index, trend in enumerate(list(trends)[:limit]):
        format_name, suffix = templates[index % len(templates)]
        urgency = "지금 제작" if trend.get("is_active") and trend.get("trend_score", 0) >= 60 else "오늘 안에"
        ideas.append(
            {
                "keyword": trend["keyword"],
                "geo": trend.get("geo", "KR"),
                "title": f"{trend['keyword']} — {suffix}",
                "format": format_name,
                "urgency": urgency,
            }
        )
    return ideas
