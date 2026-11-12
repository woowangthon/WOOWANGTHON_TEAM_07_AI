"""도구 4: plan_trip — 경로 탐색 + 엘리베이터 우회 + 출발 시각 역산 통합. 명세 §3.4"""

from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from ..data.mock_data import (
    ALTERNATIVES, DEFAULT_DETOUR_DISTANCE_M, DEFAULT_DETOUR_WALK_MIN,
    LINE_2_LOOP, STATIONS, TIMING_PARAMS, station_name,
)
from ..models import DetourInfo, PlanTripOutput, RouteInfo, RouteLeg, TripBreakdown
from .common import parse_hhmm
from .elevator import get_elevator_status, is_blocked


def _find_line2_path(start_id: str, end_id: str) -> Optional[Tuple[List[str], int]]:
    """2호선 순환선에서 외선/내선 중 짧은 경로와 정거장 수. 순환선 밖 역이면 None."""
    if start_id not in LINE_2_LOOP or end_id not in LINE_2_LOOP:
        return None
    n = len(LINE_2_LOOP)
    s, e = LINE_2_LOOP.index(start_id), LINE_2_LOOP.index(end_id)
    cw = (e - s) % n
    ccw = (s - e) % n
    if cw <= ccw:
        return [LINE_2_LOOP[(s + i) % n] for i in range(cw + 1)], cw
    return [LINE_2_LOOP[(s - i) % n] for i in range(ccw + 1)], ccw


def find_alternative(station_id: str, target_type: str = "end") -> Optional[DetourInfo]:
    """엘리베이터를 쓸 수 없는 역의 대체역을 찾습니다.

    1. ALTERNATIVES에 등록된 안내가 있으면 사용합니다.
    2. 없으면 2호선 순환선 양옆 역 중 엘리베이터 고장이 확인되지 않은 역을 고릅니다.
       (거리 정보가 없으므로 DEFAULT_DETOUR_* 기본값 사용)
    대체역 자체가 고장으로 확인되면 추천하지 않습니다.
    """
    broken_name = station_name(station_id)
    entry = ALTERNATIVES.get(station_id)
    if entry and not is_blocked(entry["alt_id"]):
        alt_id = entry["alt_id"]
        return DetourInfo(
            target_type=target_type, broken_station_id=station_id,
            alternative_station_id=alt_id, alternative_station_name=station_name(alt_id),
            surface_distance_m=entry["distance_m"] or DEFAULT_DETOUR_DISTANCE_M,
            surface_walk_minutes=entry["walk_min"] or DEFAULT_DETOUR_WALK_MIN,
            bus_tip=entry["tip"],
            reason=f"{broken_name}역 엘리베이터 이용 불가 구간 있음",
        )

    if station_id in LINE_2_LOOP:
        n = len(LINE_2_LOOP)
        idx = LINE_2_LOOP.index(station_id)
        for neighbor in (LINE_2_LOOP[(idx + 1) % n], LINE_2_LOOP[(idx - 1) % n]):
            if not is_blocked(neighbor):
                alt_name = station_name(neighbor)
                return DetourInfo(
                    target_type=target_type, broken_station_id=station_id,
                    alternative_station_id=neighbor, alternative_station_name=alt_name,
                    surface_distance_m=DEFAULT_DETOUR_DISTANCE_M,
                    surface_walk_minutes=DEFAULT_DETOUR_WALK_MIN,
                    bus_tip=f"인접한 {alt_name}역 엘리베이터 이용 권장 (거리 정보 없음)",
                    reason=f"{broken_name}역 엘리베이터 이용 불가 구간 있음",
                )
    return None


def plan_trip(
    start_id: str,
    end_id: str,
    arrive_by: Optional[str] = None,
    depart_at: Optional[str] = None,
    home_walk_min: Optional[int] = 7,
    *,
    now: Optional[datetime] = None,
) -> PlanTripOutput:
    """경로 탐색, 엘리베이터 고장 우회, 출발 시각 역산을 한 번에 계산합니다.

    총 소요(분) = 도보(home_walk_min) + 역 진입(3) + 최악 대기(배차 3 + 여유 2)
                 + 정거장 수 × 2 + 출구 엘리베이터(3)

    모드:
        arrive_by 있음 → 역산. 도착 시각이 이미 지났으면 다음 날로 간주합니다.
        arrive_by 없음 → 순방향. depart_at(없으면 현재 시각)부터 계산합니다.

    Args:
        now: 기준 시각. LLM에 노출되지 않는 인자로, 테스트나 요청의 current_time을 넣을 때 씁니다.

    Raises:
        ValueError: 모르는 역 ID이거나 시각 형식이 HH:MM이 아닐 때.

    Mock 단계 제약:
        - 2호선 순환선 안에서만 경로를 계산합니다. 순환선 밖 역이 끼면 is_possible=False.
        - 환승을 계산하지 않습니다 (transfers=0, 단일 leg).
        - 출발역·도착역의 고장을 검사해 대체역을 붙이지만, 경로·시간을 다시 계산하지는 않습니다.
        - 첫차/막차를 반영하지 않습니다.
    """
    for label, sid in (("start_id", start_id), ("end_id", end_id)):
        if sid not in STATIONS:
            raise ValueError(f"{label}: 알 수 없는 역 ID입니다 ({sid!r}). resolve_station으로 먼저 확인하세요.")
    arrive_hm = parse_hhmm(arrive_by, "arrive_by") if arrive_by else None
    depart_hm = parse_hhmm(depart_at, "depart_at") if depart_at else None

    now = (now or datetime.now()).replace(second=0, microsecond=0)
    warnings: List[str] = []

    # 1. 경로
    found = _find_line2_path(start_id, end_id)
    is_possible = found is not None
    if found:
        path, stops = found
    else:
        path, stops = [start_id, end_id], 0
        warnings.append(
            f"{station_name(start_id)}역→{station_name(end_id)}역 경로는 아직 계산을 지원하지 않습니다 "
            "(현재 2호선 순환선 구간만 지원)."
        )
    ride_min = stops * TIMING_PARAMS["minutes_per_stop"]
    route = RouteInfo(
        path=path,
        legs=[RouteLeg(line_id="2", from_id=start_id, to_id=end_id, stops=stops, ride_minutes=ride_min)] if found else [],
        total_stops=stops, transfers=0, total_duration_minutes=ride_min,
    )

    # 2. 출발역·도착역 엘리베이터 검사 및 대체역
    detours: List[DetourInfo] = []
    checks = [("end", end_id)] if start_id == end_id else [("start", start_id), ("end", end_id)]
    for role, sid in checks:
        status = get_elevator_status(sid)
        if status.overall_status not in ("PARTIAL", "UNAVAILABLE"):
            continue
        role_label = "출발역인 " if role == "start" and start_id != end_id else ""
        alt = find_alternative(sid, target_type=role)
        if alt:
            detours.append(alt)
            warnings.append(
                f"{role_label}{status.station_name}역 엘리베이터에 이용 불가 구간이 있습니다. "
                f"대체역({alt.alternative_station_name}역) 이용을 권장합니다."
            )
        else:
            warnings.append(f"{role_label}{status.station_name}역 엘리베이터에 이용 불가 구간이 있습니다.")

    # 3. 시간 계산
    home_walk = home_walk_min if home_walk_min is not None else TIMING_PARAMS["home_walk"]
    breakdown = TripBreakdown(
        home_walk=home_walk,
        station_entry_elevator=TIMING_PARAMS["station_entry_elevator"],
        worst_case_wait=TIMING_PARAMS["headway_line2"] + TIMING_PARAMS["safety_margin"],
        subway_ride=ride_min,
        transfer_walk=0,
        exit_elevator=TIMING_PARAMS["exit_elevator"],
    )
    before_board = breakdown.home_walk + breakdown.station_entry_elevator + breakdown.worst_case_wait
    total = before_board + breakdown.subway_ride + breakdown.transfer_walk + breakdown.exit_elevator

    if arrive_hm:
        arrive_dt = now.replace(hour=arrive_hm[0], minute=arrive_hm[1])
        if arrive_dt < now:
            arrive_dt += timedelta(days=1)
        leave_dt = arrive_dt - timedelta(minutes=total)
        minutes_from_now = int((leave_dt - now).total_seconds() // 60)
    else:
        leave_dt = now.replace(hour=depart_hm[0], minute=depart_hm[1]) if depart_hm else now
        arrive_dt = leave_dt + timedelta(minutes=total)
        minutes_from_now = int((leave_dt - now).total_seconds() // 60)

    return PlanTripOutput(
        is_possible=is_possible,
        departure_time=leave_dt.strftime("%H:%M"),
        board_time=(leave_dt + timedelta(minutes=before_board)).strftime("%H:%M"),
        arrival_time=arrive_dt.strftime("%H:%M"),
        minutes_from_now=minutes_from_now,
        # 지각 판정은 도착 목표가 있는 역산 모드에서만 의미가 있습니다.
        is_late=bool(arrive_hm) and minutes_from_now < 0,
        route=route,
        detours=detours,
        breakdown=breakdown,
        warnings=warnings,
    )
