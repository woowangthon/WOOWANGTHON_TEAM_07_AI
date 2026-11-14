"""도구 2·3: get_elevator_status, get_broken_elevators — 엘리베이터 상태 조회. 명세 §3.2, §3.3

상태 판정은 BE 계약(BE/docs/api-contract.md §6.2)과 같습니다.
    T = 역의 엘리베이터 수, U = 상태를 확인하지 못한 수(UNKNOWN), C = T − U
    - 판정은 확인된 설비(C)만으로 합니다. 확인되지 않은 설비를 정상으로 채우지 않습니다.
    - C의 설비가 전부 가동 → AVAILABLE, 섞임 → PARTIAL, 전부 불가 → UNAVAILABLE
    - C = 0 (전부 미확인) 또는 T = 0 (엘리베이터 정보 없음) → UNKNOWN
    - data_fresh = (T > 0 이고 U = 0)
"""

from datetime import datetime
from typing import List, Optional, Tuple

from ..data import get_data_source, station_name
from ..models import (
    BrokenElevatorsOutput, BrokenStationSummary, ElevatorSegment, ElevatorStatusOutput,
)

# BE 표준 limitations 문구 (BE/docs/api-contract.md §0.4). 문구를 바꾸면 BE와 같이 바꿉니다.
LIMIT_SOME_UNKNOWN = "일부 설비 상태가 확인되지 않아 역 전체 상태를 단정할 수 없음"
LIMIT_ALL_UNKNOWN = "확인 가능한 엘리베이터 상태가 없어 역 상태를 판정할 수 없음"
LIMIT_NO_ELEVATOR = "엘리베이터 정보가 없어 역 상태를 판정할 수 없음"
LIMIT_LIST_UNKNOWN = "일부 설비의 상태 정보가 없거나 오래되어 현재 상태를 확인하지 못함"


def judge(segments: List[ElevatorSegment]) -> Tuple[str, bool, int, List[str]]:
    """(overall_status, data_fresh, unknown_count, limitations)를 BE 규칙으로 판정합니다."""
    total = len(segments)
    if total == 0:
        return "UNKNOWN", False, 0, [LIMIT_NO_ELEVATOR]
    known = [s for s in segments if s.status != "UNKNOWN"]
    unknown = total - len(known)
    if not known:
        return "UNKNOWN", False, unknown, [LIMIT_ALL_UNKNOWN]
    down = sum(1 for s in known if s.status == "UNAVAILABLE")
    if down == 0:
        overall = "AVAILABLE"
    elif down == len(known):
        overall = "UNAVAILABLE"
    else:
        overall = "PARTIAL"
    return overall, unknown == 0, unknown, ([LIMIT_SOME_UNKNOWN] if unknown else [])


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _latest_fetch(segments: List[ElevatorSegment]) -> Tuple[Optional[str], Optional[int]]:
    """설비별 수집 시각 중 가장 최근 값과 경과 분. 설비에 수집 시각이 없으면 데이터 소스 기준 시각."""
    stamps = [(dt, s.fetched_at) for s in segments if s.fetched_at and (dt := _parse_iso(s.fetched_at))]
    if not stamps:
        return get_data_source().data_updated_at()
    dt, raw = max(stamps)
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    return raw, max(0, int((now - dt).total_seconds() // 60))


def get_elevator_status(station_id: str) -> ElevatorStatusOutput:
    """특정 역의 설비 단위 엘리베이터 상태와 BE 규칙으로 판정한 역 상태를 반환합니다.

    지원 역인데 엘리베이터 데이터가 없으면 오류 없이 UNKNOWN을 반환합니다.

    Raises:
        ValueError: 지원 역 목록에 없는 ID (범위 밖 역을 지원 역처럼 안내하지 않도록).
        DataUnavailableError: 데이터 소스(BE)에 연결할 수 없을 때.
    """
    src = get_data_source()
    st = src.station(station_id)
    if st is None:
        raise ValueError(f"station_id: 알 수 없는 역 ID입니다 ({station_id!r}). resolve_station으로 먼저 확인하세요.")
    segments = [ElevatorSegment(**e) for e in (src.elevators(station_id) or [])]
    overall, fresh, unknown, limitations = judge(segments)
    # 역 자체의 한계 안내 (BE: 1호선은 일부 구간만 수록 등)
    limitations += [x for x in st.get("limitations", []) if x not in limitations]
    updated_at, freshness = _latest_fetch(segments) if segments else (None, None)
    return ElevatorStatusOutput(
        station_id=station_id, station_name=station_name(station_id),
        overall_status=overall, data_fresh=fresh,
        total_count=len(segments), unknown_count=unknown,
        updated_at=updated_at, freshness_minutes=freshness,
        limitations=limitations, segments=segments,
    )


def describe_status(status: dict) -> str:
    """역 상태 안내 문장 (응답 빌더·폴백 엔진 공용). status는 ElevatorStatusOutput의 dict.

    BE 표시 규칙(§6.2): data_fresh가 False면 "정상 운행"처럼 단정하지 않고 확인된 범위를 밝힙니다.
    limitations 문장은 호출하는 쪽이 따로 덧붙입니다(format_limitations).
    """
    name, overall = status["station_name"], status["overall_status"]
    total, unknown = status.get("total_count", 0), status.get("unknown_count", 0)
    if overall == "AVAILABLE":
        if status.get("data_fresh"):
            return f"현재 {name}역의 엘리베이터 {total}대가 모두 가동 중으로 확인됩니다."
        return (f"{name}역 엘리베이터 {total}대 중 상태가 확인된 {total - unknown}대는 가동 중입니다. "
                f"나머지 {unknown}대는 상태를 확인하지 못해 역 전체가 이용 가능하다고 단정할 수 없습니다.")
    if overall in ("PARTIAL", "UNAVAILABLE"):
        broken = [s for s in status["segments"] if s["status"] == "UNAVAILABLE"]
        where = ", ".join(f"{s['location']}({s['floors']})" if s.get("floors") else s["location"] for s in broken)
        if unknown:
            return (f"현재 {name}역 엘리베이터 {total}대 중 {len(broken)}대를 이용할 수 없는 것으로 확인됩니다: "
                    f"{where}. 나머지 중 {unknown}대는 상태를 확인하지 못했습니다.")
        scope = "전체" if overall == "UNAVAILABLE" else "일부"
        return f"현재 {name}역 엘리베이터 {scope} 구간을 이용할 수 없습니다: {where}."
    return f"{name}역의 엘리베이터 상태를 확인할 수 없습니다. 이용 가능하다고 판단하지 마시고, 역에 문의해 주세요."


def format_limitations(limitations: List[str]) -> List[str]:
    """limitations를 안내 문장 줄로 만듭니다 (중복 제거, 순서 유지)."""
    seen, lines = set(), []
    for item in limitations:
        if item and item not in seen:
            seen.add(item)
            lines.append(f"※ {item}")
    return lines


# -----------------------------------------------------------------------------
# 3. get_broken_elevators
# -----------------------------------------------------------------------------
def get_broken_elevators(line_id: Optional[str] = None) -> BrokenElevatorsOutput:
    """이용 불가로 확인된 엘리베이터가 있는 역 목록을 반환합니다.

    상태를 확인하지 못한 설비는 고장으로 세지 않습니다. 그런 설비가 하나라도 있으면
    목록이 전부가 아닐 수 있으므로 limitations에 BE 표준 문구를 넣습니다.
    정렬: 전체 불가(UNAVAILABLE) 먼저, 그다음 불가 설비 수가 많은 순.
    """
    src = get_data_source()
    outages: List[BrokenStationSummary] = []
    any_unknown = False
    latest: List[ElevatorSegment] = []
    for sid, elevs in src.all_elevators().items():
        st = src.station(sid)
        if not st or (line_id and line_id not in st["lines"]):
            continue
        segments = [ElevatorSegment(**e) for e in elevs]
        overall, _, unknown, _ = judge(segments)
        any_unknown = any_unknown or unknown > 0
        latest += [s for s in segments if s.fetched_at]
        if overall not in ("PARTIAL", "UNAVAILABLE"):
            continue
        broken = [s for s in segments if s.status == "UNAVAILABLE"]
        outages.append(BrokenStationSummary(
            station_id=sid, station_name=st["name"], line_ids=st["lines"],
            broken_count=len(broken), total_count=len(segments), unknown_count=unknown,
            status=overall, details=[s.model_dump() for s in broken],
        ))

    outages.sort(key=lambda o: (o.status != "UNAVAILABLE", -o.broken_count))
    return BrokenElevatorsOutput(
        line_id=line_id, count=len(outages),
        updated_at=_latest_fetch(latest)[0],
        limitations=[LIMIT_LIST_UNKNOWN] if any_unknown else [],
        outages=outages,
    )


def is_blocked(station_id: str) -> bool:
    """엘리베이터에 이용 불가로 확인된 설비가 있는지 (PARTIAL 또는 UNAVAILABLE)."""
    return get_elevator_status(station_id).overall_status in ("PARTIAL", "UNAVAILABLE")
