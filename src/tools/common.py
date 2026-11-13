"""도구 공용 유틸리티: 시각 파싱.

데이터 기준 시각은 데이터 소스가 정합니다 (DataSource.data_updated_at).
"""

import re
from typing import Tuple


def parse_hhmm(value: str, field: str = "time") -> Tuple[int, int]:
    """'HH:MM'(24시간제)을 (시, 분)으로 변환합니다. 형식이 틀리면 ValueError."""
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", (value or "").strip())
    if not m:
        raise ValueError(f"{field}는 HH:MM(00:00~23:59) 형식이어야 합니다: {value!r}")
    return int(m.group(1)), int(m.group(2))
