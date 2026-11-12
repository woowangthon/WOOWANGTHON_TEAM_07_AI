"""도구 공용 유틸리티: 시각 파싱, Mock 데이터 기준 시각."""

import re
from datetime import datetime, timedelta
from typing import Optional, Tuple

# Mock 데이터의 기준 시각은 '현재 − 5분'으로 고정합니다.
MOCK_FRESHNESS_MIN = 5


def mock_updated_at(now: Optional[datetime] = None) -> str:
    """Mock 데이터 기준 시각 (ISO-8601, KST)."""
    base = now or datetime.now()
    return (base - timedelta(minutes=MOCK_FRESHNESS_MIN)).strftime("%Y-%m-%dT%H:%M:00+09:00")


def parse_hhmm(value: str, field: str = "time") -> Tuple[int, int]:
    """'HH:MM'(24시간제)을 (시, 분)으로 변환합니다. 형식이 틀리면 ValueError."""
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", (value or "").strip())
    if not m:
        raise ValueError(f"{field}는 HH:MM(00:00~23:59) 형식이어야 합니다: {value!r}")
    return int(m.group(1)), int(m.group(2))
