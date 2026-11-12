"""도구 1: resolve_station — 역 이름 정규화 및 서비스 범위 판정. 명세 §3.1"""

import re

from ..data.mock_data import ALIASES, OUT_OF_COVERAGE_KEYWORDS, STATIONS
from ..models import ResolveStationOutput


def _supported(station_id: str, confidence: float) -> ResolveStationOutput:
    st = STATIONS[station_id]
    return ResolveStationOutput(
        station_id=st["id"], name=st["name"], lines=st["lines"],
        coverage="SUPPORTED", confidence=confidence,
    )


def resolve_station(query: str) -> ResolveStationOutput:
    """사용자의 역 이름 표현을 고유 station_id로 정규화하고 커버리지를 판정합니다.

    공백과 노선 표기("2호선")를 지우고 끝의 '역'을 뗀 뒤, 아래 순서로 처음 일치하는 규칙을 반환합니다.
      1. 표준 역명 또는 역 ID와 정확히 일치  → SUPPORTED, 1.0
      2. 별칭 사전 일치                    → SUPPORTED, 0.95
      3. 미지원 역/노선/지역 키워드 포함     → OUT_OF_COVERAGE (station_id=None), 0.9
      4. 표준 역명의 앞부분과 일치하는 역이 정확히 하나 (2글자 이상)
                                          → SUPPORTED, 0.8
      5. 그 외 (후보가 여러 개인 경우 포함)  → NOT_FOUND, 0.0
    """
    clean = re.sub(r"\s+", "", query or "")
    # "선릉 2호선", "시청(1호선)" 같은 노선 표기 제거
    clean = re.sub(r"\(?[1-9]호선\)?", "", clean)
    base = re.sub(r"역$", "", clean)

    for sid, st in STATIONS.items():
        if base == st["name"] or clean == sid:
            return _supported(sid, 1.0)

    if base in ALIASES:
        return _supported(ALIASES[base], 0.95)

    for kw in OUT_OF_COVERAGE_KEYWORDS:
        if kw.lower() in clean.lower():
            return ResolveStationOutput(
                station_id=None, name=kw, lines=[],
                coverage="OUT_OF_COVERAGE", confidence=0.9,
            )

    if len(base) >= 2:
        candidates = [sid for sid, st in STATIONS.items() if st["name"].startswith(base)]
        if len(candidates) == 1:
            return _supported(candidates[0], 0.8)

    return ResolveStationOutput(
        station_id=None, name=query, lines=[],
        coverage="NOT_FOUND", confidence=0.0,
    )
