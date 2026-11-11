"""AI 파트 4대 도구(Tools) 구현체.

명세: AI/docs/AI_DEVELOPMENT_SPEC.md
규칙: LLM은 해석만 하고 실제 계산은 도구 함수가 수행합니다.
"""

from datetime import datetime, timedelta
import re
from typing import Optional, List, Tuple
from .models import (
    ResolveStationInput, ResolveStationOutput,
    ElevatorStatusInput, ElevatorStatusOutput, ElevatorSegment,
    BrokenElevatorsInput, BrokenElevatorsOutput, BrokenStationSummary,
    PlanTripInput, PlanTripOutput, RouteInfo, RouteLeg, DetourInfo, TripBreakdown
)
from .mock_data import (
    STATIONS, ALIASES, ELEVATORS, ALTERNATIVE_TIPS,
    LINE_2_LOOP, LINES, TIMING_PARAMS
)


# -----------------------------------------------------------------------------
# 1. resolve_station
# -----------------------------------------------------------------------------
def resolve_station(query: str) -> ResolveStationOutput:
    """사용자의 자연어 검색어를 정규화하여 고유 station_id 및 커버리지를 판정합니다."""
    q = query.strip()
    clean_q = re.sub(r"\s+", "", q)
    base_name = re.sub(r"역$", "", clean_q)

    # 1. 별칭 사전 매칭
    if clean_q in ALIASES:
        sid = ALIASES[clean_q]
        st = STATIONS[sid]
        return ResolveStationOutput(
            station_id=st["id"], name=st["name"], lines=st["lines"],
            coverage="SUPPORTED", confidence=1.0
        )
    if base_name in ALIASES:
        sid = ALIASES[base_name]
        st = STATIONS[sid]
        return ResolveStationOutput(
            station_id=st["id"], name=st["name"], lines=st["lines"],
            coverage="SUPPORTED", confidence=0.95
        )

    # 2. 표준 역명 매칭
    for sid, st in STATIONS.items():
        if clean_q == st["name"] or base_name == st["name"] or clean_q == sid:
            return ResolveStationOutput(
                station_id=st["id"], name=st["name"], lines=st["lines"],
                coverage="SUPPORTED", confidence=1.0
            )

    # 3. 미지원 노선 (신분당선, 9호선, KTX 등) 판정
    out_of_coverage_keywords = ["판교", "정자", "광교", "신분당선", "9호선", "봉은사", "선정릉", "여의도", "KTX", "부산", "대전"]
    for kw in out_of_coverage_keywords:
        if kw in clean_q:
            return ResolveStationOutput(
                station_id=None, name=kw, lines=[],
                coverage="OUT_OF_COVERAGE", confidence=0.9
            )

    # 4. 부분 일치 검색
    for sid, st in STATIONS.items():
        if base_name and (base_name in st["name"] or st["name"] in base_name):
            return ResolveStationOutput(
                station_id=st["id"], name=st["name"], lines=st["lines"],
                coverage="SUPPORTED", confidence=0.8
            )

    return ResolveStationOutput(
        station_id=None, name=query, lines=[],
        coverage="NOT_FOUND", confidence=0.0
    )


# -----------------------------------------------------------------------------
# 2. get_elevator_status
# -----------------------------------------------------------------------------
def get_elevator_status(station_id: str) -> ElevatorStatusOutput:
    """특정 역의 구간 단위 엘리베이터 상태와 기준 시각을 반환합니다."""
    st = STATIONS.get(station_id)
    st_name = st["name"] if st else station_id
    now = datetime.now()
    updated_at = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:00+09:00")

    raw_elevs = ELEVATORS.get(station_id)
    if not raw_elevs:
        return ElevatorStatusOutput(
            station_id=station_id,
            station_name=st_name,
            overall_status="UNKNOWN",
            updated_at=updated_at,
            freshness_minutes=5,
            segments=[]
        )

    segments: List[ElevatorSegment] = []
    down_count = 0
    for e in raw_elevs:
        seg = ElevatorSegment(
            segment_id=e["segment_id"],
            type=e["type"],
            location=e["location"],
            floors=e["floors"],
            status=e["status"],
            reason=e.get("reason", "")
        )
        segments.append(seg)
        if seg.status != "AVAILABLE":
            down_count += 1

    if down_count == 0:
        overall = "AVAILABLE"
    elif down_count == len(segments):
        overall = "UNAVAILABLE"
    else:
        overall = "PARTIAL"

    return ElevatorStatusOutput(
        station_id=station_id,
        station_name=st_name,
        overall_status=overall,
        updated_at=updated_at,
        freshness_minutes=5,
        segments=segments
    )


# -----------------------------------------------------------------------------
# 3. get_broken_elevators
# -----------------------------------------------------------------------------
def get_broken_elevators(line_id: Optional[str] = None) -> BrokenElevatorsOutput:
    """고장 또는 점검 중인 엘리베이터가 있는 역 목록을 반환합니다."""
    now = datetime.now()
    updated_at = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:00+09:00")
    outages: List[BrokenStationSummary] = []

    for sid, elev_list in ELEVATORS.items():
        st = STATIONS.get(sid)
        if not st:
            continue
        if line_id and line_id not in st.get("lines", []):
            continue

        broken = [e for e in elev_list if e.get("status") != "AVAILABLE"]
        if broken:
            status = "UNAVAILABLE" if len(broken) == len(elev_list) else "PARTIAL"
            outages.append(BrokenStationSummary(
                station_id=sid,
                station_name=st["name"],
                line_ids=st["lines"],
                broken_count=len(broken),
                total_count=len(elev_list),
                status=status,
                details=broken
            ))

    # 전체 고장(UNAVAILABLE) 우선 정렬
    outages.sort(key=lambda x: (0 if x.status == "UNAVAILABLE" else 1, -x.broken_count))

    return BrokenElevatorsOutput(
        line_id=line_id,
        count=len(outages),
        updated_at=updated_at,
        outages=outages
    )


# -----------------------------------------------------------------------------
# 4. plan_trip (경로 탐색 + 엘리베이터 우회 + 출발 시각 역산 통합)
# -----------------------------------------------------------------------------
def _find_2line_path(start_id: str, end_id: str) -> Tuple[List[str], int]:
    """2호선 순환선 기준 내선/외선 중 더 짧은 경로와 정거장 수를 계산합니다."""
    if start_id not in LINE_2_LOOP or end_id not in LINE_2_LOOP:
        # 순환선 외 역인 경우 기본 직결/인접 경로 반환
        return [start_id, end_id], 1

    n = len(LINE_2_LOOP)
    s_idx = LINE_2_LOOP.index(start_id)
    e_idx = LINE_2_LOOP.index(end_id)

    # 시계방향 (외선)
    cw_stops = (e_idx - s_idx) % n
    cw_path = [LINE_2_LOOP[(s_idx + i) % n] for i in range(cw_stops + 1)]

    # 반시계방향 (내선)
    ccw_stops = (s_idx - e_idx) % n
    ccw_path = [LINE_2_LOOP[(s_idx - i) % n] for i in range(ccw_stops + 1)]

    if cw_stops <= ccw_stops:
        return cw_path, cw_stops
    else:
        return ccw_path, ccw_stops


def _find_alternative(station_id: str) -> Optional[DetourInfo]:
    """고장 역에 대한 인접 정상 대체역을 탐색합니다."""
    key_prefix = f"{station_id}>"
    for k, tip in ALTERNATIVE_TIPS.items():
        if k.startswith(key_prefix):
            alt_id = k.split(">")[1]
            alt_st = STATIONS.get(alt_id)
            alt_name = alt_st["name"] if alt_st else alt_id
            return DetourInfo(
                target_type="end",
                broken_station_id=station_id,
                alternative_station_id=alt_id,
                alternative_station_name=alt_name,
                surface_distance_m=800,
                surface_walk_minutes=10,
                bus_tip=tip,
                reason=f"{STATIONS.get(station_id, {}).get('name', station_id)}역 엘리베이터 점검/고장"
            )

    # 2호선 인접역 기본 우회
    if station_id in LINE_2_LOOP:
        idx = LINE_2_LOOP.index(station_id)
        alt_id = LINE_2_LOOP[(idx + 1) % len(LINE_2_LOOP)]
        alt_st = STATIONS.get(alt_id)
        alt_name = alt_st["name"] if alt_st else alt_id
        return DetourInfo(
            target_type="end",
            broken_station_id=station_id,
            alternative_station_id=alt_id,
            alternative_station_name=alt_name,
            surface_distance_m=950,
            surface_walk_minutes=12,
            bus_tip=f"인접한 {alt_name}역 엘리베이터 이용 권장",
            reason=f"{STATIONS.get(station_id, {}).get('name', station_id)}역 엘리베이터 이용 불가"
        )
    return None


def plan_trip(
    start_id: str,
    end_id: str,
    arrive_by: Optional[str] = None,
    depart_at: Optional[str] = None,
    home_walk_min: Optional[int] = 7
) -> PlanTripOutput:
    """경로 탐색, 엘리베이터 고장 우회, 출발 시각 역산을 원스톱으로 계산합니다."""
    home_walk = home_walk_min if home_walk_min is not None else TIMING_PARAMS["home_walk"]
    entry_wait = TIMING_PARAMS["station_entry_elevator"]
    worst_wait = TIMING_PARAMS["headway_line2"] + TIMING_PARAMS["safety_margin"]  # 3 + 2 = 5분 (보수적)
    exit_walk = TIMING_PARAMS["exit_elevator"]

    # 1. 경로 탐색
    path, stops = _find_2line_path(start_id, end_id)
    ride_min = stops * TIMING_PARAMS["minutes_per_stop"]

    route = RouteInfo(
        path=path,
        legs=[
            RouteLeg(
                line_id="2",
                from_id=start_id,
                to_id=end_id,
                stops=stops,
                ride_minutes=ride_min
            )
        ],
        total_stops=stops,
        transfers=0,
        total_duration_minutes=ride_min
    )

    # 2. 엘리베이터 상태 검사 및 대체역 탐색
    detours: List[DetourInfo] = []
    warnings: List[str] = []

    dest_status = get_elevator_status(end_id)
    if dest_status.overall_status in ["PARTIAL", "UNAVAILABLE"]:
        alt = _find_alternative(end_id)
        if alt:
            detours.append(alt)
            warnings.append(
                f"{dest_status.station_name}역 엘리베이터가 {dest_status.overall_status} 상태입니다. 대체역({alt.alternative_station_name}역) 이용을 권장합니다."
            )

    start_status = get_elevator_status(start_id)
    if start_status.overall_status in ["PARTIAL", "UNAVAILABLE"]:
        warnings.append(f"출발역인 {start_status.station_name}역 엘리베이터에 이용 불가 구간이 있습니다.")

    # 3. 시간 계산 (역산 또는 순방향)
    breakdown = TripBreakdown(
        home_walk=home_walk,
        station_entry_elevator=entry_wait,
        worst_case_wait=worst_wait,
        subway_ride=ride_min,
        transfer_walk=0,
        exit_elevator=exit_walk
    )
    total_transit_min = home_walk + entry_wait + worst_wait + ride_min + exit_walk

    now = datetime.now()
    base_time = now

    if arrive_by:
        # 역산 모드: arrive_by 기준 역산
        try:
            parts = arrive_by.split(":")
            ah, am = int(parts[0]), int(parts[1])
            target_dt = now.replace(hour=ah, minute=am, second=0, microsecond=0)
            if target_dt < now:
                target_dt += timedelta(days=1)
            
            leave_dt = target_dt - timedelta(minutes=total_transit_min)
            board_dt = leave_dt + timedelta(minutes=home_walk + entry_wait + worst_wait)
            arrive_dt = target_dt

            mins_from_now = int((leave_dt - now).total_seconds() / 60)
            is_late = mins_from_now < 0

            return PlanTripOutput(
                is_possible=True,
                departure_time=leave_dt.strftime("%H:%M"),
                board_time=board_dt.strftime("%H:%M"),
                arrival_time=arrive_dt.strftime("%H:%M"),
                minutes_from_now=mins_from_now,
                is_late=is_late,
                route=route,
                detours=detours,
                breakdown=breakdown,
                warnings=warnings
            )
        except Exception:
            pass

    # 순방향 모드 (지금 출발 또는 depart_at)
    if depart_at:
        parts = depart_at.split(":")
        dh, dm = int(parts[0]), int(parts[1])
        base_time = now.replace(hour=dh, minute=dm, second=0, microsecond=0)

    leave_dt = base_time
    board_dt = leave_dt + timedelta(minutes=home_walk + entry_wait + worst_wait)
    arrive_dt = leave_dt + timedelta(minutes=total_transit_min)

    return PlanTripOutput(
        is_possible=True,
        departure_time=leave_dt.strftime("%H:%M"),
        board_time=board_dt.strftime("%H:%M"),
        arrival_time=arrive_dt.strftime("%H:%M"),
        minutes_from_now=0,
        is_late=False,
        route=route,
        detours=detours,
        breakdown=breakdown,
        warnings=warnings
    )

