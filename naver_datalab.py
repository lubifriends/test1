"""Naver DataLab search trend collector with explicit age filters."""

from __future__ import annotations

import argparse
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

import requests


API_URL = "https://openapi.naver.com/v1/datalab/search"
AGE_GROUPS = {
    "2": "13~18세",
    "3": "19~24세",
    "4": "25~29세",
}


class NaverDataLabError(RuntimeError):
    """Raised when Naver DataLab rejects or cannot complete a request."""


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


class NaverDataLabCollector:
    """Fetch daily/weekly/monthly relative search ratios from Naver DataLab."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout: int = 20,
    ) -> None:
        self.client_id = client_id or os.getenv("NAVER_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("NAVER_CLIENT_SECRET", "")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def fetch(
        self,
        keywords: list[str],
        start_date: date,
        end_date: date,
        time_unit: str = "date",
        age_codes: tuple[str, ...] = ("2", "3", "4"),
    ) -> list[dict[str, Any]]:
        if not self.configured:
            raise NaverDataLabError("NAVER_CLIENT_ID와 NAVER_CLIENT_SECRET이 필요합니다.")
        if time_unit not in {"date", "week", "month"}:
            raise ValueError("time_unit must be date, week, or month")

        clean_keywords = list(dict.fromkeys(k.strip() for k in keywords if k.strip()))
        records: list[dict[str, Any]] = []
        headers = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
            "Content-Type": "application/json",
        }

        # Naver accepts at most five keyword groups per request.  Calling each
        # age separately is intentional: it preserves the requested age bands.
        for batch in _chunks(clean_keywords, 5):
            keyword_groups = [{"groupName": keyword, "keywords": [keyword]} for keyword in batch]
            for age_code in age_codes:
                if age_code not in AGE_GROUPS:
                    raise ValueError(f"Unsupported Naver age code: {age_code}")
                payload = {
                    "startDate": start_date.isoformat(),
                    "endDate": end_date.isoformat(),
                    "timeUnit": time_unit,
                    "keywordGroups": keyword_groups,
                    "ages": [age_code],
                }
                try:
                    response = requests.post(
                        API_URL,
                        headers=headers,
                        json=payload,
                        timeout=self.timeout,
                    )
                    response.raise_for_status()
                    body = response.json()
                except (requests.RequestException, ValueError) as exc:
                    detail = getattr(response, "text", "")[:300] if "response" in locals() else ""
                    raise NaverDataLabError(
                        f"네이버 데이터랩 요청 실패(age={age_code}): {exc} {detail}"
                    ) from exc

                for result in body.get("results", []):
                    for point in result.get("data", []):
                        records.append(
                            {
                                "keyword": result.get("title", ""),
                                "age_code": age_code,
                                "age_label": AGE_GROUPS[age_code],
                                "period": point.get("period"),
                                "ratio": float(point.get("ratio", 0)),
                                "time_unit": time_unit,
                                "source": "naver_datalab",
                                "geo": "KR",
                            }
                        )
        return records

    def fetch_last_7_days(self, keywords: list[str]) -> list[dict[str, Any]]:
        today = date.today()
        return self.fetch(keywords, today - timedelta(days=6), today, "date")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Naver age trends into SQLite")
    parser.add_argument("keywords", nargs="+", help="Keywords (up to any count; batched by five)")
    parser.add_argument("--db", default="trends.db", help="SQLite database path")
    args = parser.parse_args()

    from database import init_db, log_collection_run, save_naver_age_trends

    init_db(args.db)
    started = datetime.now(timezone.utc)
    try:
        rows = NaverDataLabCollector().fetch_last_7_days(args.keywords)
        save_naver_age_trends(args.db, rows, collected_at=started, is_mock=False)
        log_collection_run(args.db, "naver", "success", len(rows), started_at=started, geo="KR")
        print(f"Collected {len(rows)} Naver DataLab points.")
    except Exception as exc:
        log_collection_run(args.db, "naver", "failed", 0, str(exc), started_at=started, geo="KR")
        raise


if __name__ == "__main__":
    main()
