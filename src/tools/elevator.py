"""도구 2·3: get_elevator_status, get_broken_elevators — 엘리베이터 상태 조회. 명세 §3.2, §3.3"""

from typing import List, Optional

from ..data.mock_data import ELEVATORS, STATIONS, station_name
from ..models import (
    BrokenElevatorsOutput, BrokenStationSummary, ElevatorSegment, ElevatorStatusOutput,
)
from .common import MOCK_FRESHNESS_MIN, mock_updated_at


def get_elevator_status(station_id: str) -> ElevatorStatusOutput:
    """특정 역의 구간 단위 엘리베이터 상태와 기준 시각을 반환합니다.

    overall_status는 고장(AVAILABLE이 아닌) 구간 개수로 정합니다:
    0개 → AVAILABLE, 전부 → UNAVAILABLE, 그 외 → PARTIAL.
    엘리베이터 데이터가 없는 역(또는 모르는 ID)은 오류 없이 UNKNOWN을 반환합니다.
    """
    updated_at = mock_updated_at()
    raw = ELEVATORS.get(station_id)
    if not raw:
        return ElevatorStatusOutput(
            station_id=station_id, station_name=station_name(station_id),
            overall_status="UNKNOWN", updated_at=updated_at,
            freshness_minutes=MOCK_FRESHNESS_MIN, segments=[],
        )

    segments = [ElevatorSegment(**e) for e in raw]
    down = sum(1 for s in segments if s.status != "AVAILABLE")
    if down == 0:
        overall = "AVAILABLE"
    elif down == len(segments):
        overall = "UNAVAILABLE"
    else:
        overall = "PARTIAL"

    return ElevatorStatusOutput(
        station_id=station_id, station_name=station_name(station_id),
        overall_status=overall, updated_at=updated_at,
        freshness_minutes=MOCK_FRESHNESS_MIN, segments=segments,
    )


# -----------------------------------------------------------------------------
# 3. get_broken_elevators
# -----------------------------------------------------------------------------
def get_broken_elevators(line_id: Optional[str] = None) -> BrokenElevatorsOutput:
    """고장 또는 점검 중인 엘리베이터가 있는 역 목록을 반환합니다.

    정렬: 전체 고장(UNAVAILABLE) 먼저, 그다음 고장 구간 수가 많은 순.
    """
    outages: List[BrokenStationSummary] = []
    for sid, elevs in ELEVATORS.items():
        st = STATIONS.get(sid)
        if not st or (line_id and line_id not in st["lines"]):
            continue
        broken = [e for e in elevs if e["status"] != "AVAILABLE"]
        if not broken:
            continue
        outages.append(BrokenStationSummary(
            station_id=sid, station_name=st["name"], line_ids=st["lines"],
            broken_count=len(broken), total_count=len(elevs),
            status="UNAVAILABLE" if len(broken) == len(elevs) else "PARTIAL",
            details=broken,
        ))

    outages.sort(key=lambda o: (o.status != "UNAVAILABLE", -o.broken_count))
    return BrokenElevatorsOutput(
        line_id=line_id, count=len(outages),
        updated_at=mock_updated_at(), outages=outages,
    )


def is_blocked(station_id: str) -> bool:
    """엘리베이터에 이용 불가 구간이 있는지 (PARTIAL 또는 UNAVAILABLE)."""
    return get_elevator_status(station_id).overall_status in ("PARTIAL", "UNAVAILABLE")
