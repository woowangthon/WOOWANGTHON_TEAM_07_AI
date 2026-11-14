"""도구 4: plan_trip — 경로 탐색 + 엘리베이터 우회 + 출발 시각 역산 통합. 명세 §3.4"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from ..data import DataUnavailableError, get_data_source, station_name
from ..models import DetourInfo, PlanTripOutput, RouteInfo, RouteLeg, TripBreakdown
from .common import parse_hhmm
from .elevator import get_elevator_status

# 추정 계산 안내 (BE /api/departure-time의 limitations와 같은 취지). AI가 직접 역산할 때만 붙입니다.
LIMIT_ESTIMATED = "열차 시간표와 실제 경로 탐색을 쓰지 않은 추정 계산이며 탑승 가능한 열차는 확인되지 않음"

# BE /api/departure-time limitations 중 plan_trip이 전하지 않는 문구 (BE status_messages.py와 같은 문장)
BE_LIMIT_ELEVATOR_UNCONFIRMED = "역·설비 상태 정보가 입력되지 않아 엘리베이터 경고를 확인하지 못함"
BE_LIMIT_ALREADY_PAST = "추천 출발 시각이 현재 시각보다 이미 지났음"

KST = timezone(timedelta(hours=9))
KST_OFFSET = "+09:00"


def _unconfirmed_alt(alt_name: str) -> str:
    return f"대체역 {alt_name}역의 엘리베이터 상태는 확인되지 않음"


def find_alternatives(station_id: str, target_type: str = "end") -> Tuple[List[DetourInfo], List[str]]:
    """엘리베이터를 쓸 수 없는 역의 대체역 후보와 limitations를 반환합니다 (추천 순).

    데이터 소스가 대체역을 계산하면(BE) 그 결과를 그대로 씁니다.
    계산하지 않으면(Mock):
      1. 등록된 대체역 안내 — 대체역이 고장으로 확인되면 제외. 상태가 미확인이면 limitations에 밝힘
      2. 없으면 인접 역 중 엘리베이터 가동이 확인된 역(AVAILABLE·PARTIAL)
    거리·도보 시간은 확인된 값만 넣고 기본값으로 채우지 않습니다.
    """
    src = get_data_source()
    broken_name = station_name(station_id)
    reason = f"{broken_name}역 엘리베이터 이용 불가 구간 있음"

    calc = src.alternative_candidates(station_id)
    if calc is not None:
        detours = [
            DetourInfo(
                target_type=target_type, broken_station_id=station_id,
                alternative_station_id=c["id"], alternative_station_name=c["name"],
                stops_away=c.get("stops"), same_line=c.get("same_line"),
                alternative_status=c.get("status") or "UNKNOWN", reason=reason,
            )
            for c in calc["candidates"]
        ]
        return detours, list(calc.get("limitations") or [])

    entry = src.registered_alternative(station_id)
    if entry:
        alt_id = entry["alt_id"]
        alt_status = get_elevator_status(alt_id)
        if alt_status.overall_status not in ("PARTIAL", "UNAVAILABLE"):
            alt_name = station_name(alt_id)
            limitations = [] if alt_status.overall_status == "AVAILABLE" else [_unconfirmed_alt(alt_name)]
            return [DetourInfo(
                target_type=target_type, broken_station_id=station_id,
                alternative_station_id=alt_id, alternative_station_name=alt_name,
                surface_distance_m=entry["distance_m"], surface_walk_minutes=entry["walk_min"],
                alternative_status=alt_status.overall_status, bus_tip=entry["tip"], reason=reason,
            )], limitations

    for neighbor in src.neighbor_stations(station_id):
        status = get_elevator_status(neighbor)
        if status.overall_status in ("AVAILABLE", "PARTIAL"):
            alt_name = station_name(neighbor)
            return [DetourInfo(
                target_type=target_type, broken_station_id=station_id,
                alternative_station_id=neighbor, alternative_station_name=alt_name,
                stops_away=1, alternative_status=status.overall_status,
                bus_tip=f"인접한 {alt_name}역 엘리베이터 이용 권장 (지상 이동 거리 정보 없음)", reason=reason,
            )], []
    return [], []


def find_alternative(station_id: str, target_type: str = "end") -> Optional[DetourInfo]:
    """가장 먼저 추천하는 대체역 하나. 없으면 None."""
    detours, _ = find_alternatives(station_id, target_type)
    return detours[0] if detours else None


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

    출발 시각 역산 위치:
        데이터 소스가 역산하면(BE `POST /api/departure-time`) 그 결과 시각을 씁니다 (time_source="BE").
        보내는 분 값(도보·역 진입·최악 대기·열차 이동·출구)은 위 공식의 AI 값이므로 결과는 직접 계산과 같습니다.
        역산 모드이고 경로가 있으며 출발역≠도착역일 때만 호출합니다. 그 밖에는 직접 계산 (time_source="AI").

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
    src = get_data_source()
    timing = src.timing()
    for label, sid in (("start_id", start_id), ("end_id", end_id)):
        if src.station(sid) is None:
            raise ValueError(f"{label}: 알 수 없는 역 ID입니다 ({sid!r}). resolve_station으로 먼저 확인하세요.")
    arrive_hm = parse_hhmm(arrive_by, "arrive_by") if arrive_by else None
    depart_hm = parse_hhmm(depart_at, "depart_at") if depart_at else None

    now = (now or datetime.now()).replace(second=0, microsecond=0)
    warnings: List[str] = []

    # 1. 경로
    found = src.find_route(start_id, end_id)
    is_possible = found is not None
    if found:
        path, stops, line_id = found
    else:
        path, stops, line_id = [start_id, end_id], 0, ""
        warnings.append(
            f"{station_name(start_id)}역→{station_name(end_id)}역 경로는 아직 계산을 지원하지 않습니다 "
            + ("(경로 데이터(BE 경로 API) 연동 전이라 경로·출발 시각은 안내하지 않습니다)."
               if getattr(src, "route_estimate", True) is False else "(현재 2호선 순환선 구간만 지원).")
        )
    ride_min = stops * timing["minutes_per_stop"]
    route = RouteInfo(
        path=path,
        legs=[RouteLeg(line_id=line_id, from_id=start_id, to_id=end_id, stops=stops, ride_minutes=ride_min)] if found else [],
        total_stops=stops, transfers=0, total_duration_minutes=ride_min,
    )

    # 2. 출발역·도착역 엘리베이터 검사 및 대체역
    detours: List[DetourInfo] = []
    limitations: List[str] = []
    same_station = start_id == end_id
    checks = [("end", end_id)] if same_station else [("start", start_id), ("end", end_id)]
    for role, sid in checks:
        status = get_elevator_status(sid)
        role_label = "출발역인 " if role == "start" and not same_station else ""
        if status.overall_status == "UNKNOWN":
            # 정상으로 간주하지 않는다 (BE 계약 §6.3). 대체역 조회는 이용 불가가 확인된 경우만.
            limitations.append(f"{role_label}{status.station_name}역 엘리베이터 상태를 확인하지 못함")
            continue
        if not status.data_fresh and not same_station:
            # 대체역만 묻는 경우는 역 상태 안내에 이미 같은 문장이 있다
            limitations.append(f"{status.station_name}역: {status.limitations[0]}")
        if status.overall_status not in ("PARTIAL", "UNAVAILABLE"):
            continue
        alts, alt_limits = find_alternatives(sid, target_type=role)
        limitations += alt_limits
        if alts:
            # 대체역만 묻는 경우(출발=도착)는 후보를 모두, 경로 계산은 첫 후보만 붙인다.
            detours += alts if same_station else alts[:1]
            warnings.append(
                f"{role_label}{status.station_name}역 엘리베이터에 이용 불가 구간이 있습니다. "
                f"대체역({alts[0].alternative_station_name}역) 이용을 권장합니다."
            )
        else:
            warnings.append(f"{role_label}{status.station_name}역 엘리베이터에 이용 불가 구간이 있습니다.")

    # 3. 시간 계산
    home_walk = home_walk_min if home_walk_min is not None else timing["home_walk"]
    breakdown = TripBreakdown(
        home_walk=home_walk,
        station_entry_elevator=timing["station_entry_elevator"],
        worst_case_wait=timing["headway_line2"] + timing["safety_margin"],
        subway_ride=ride_min,
        transfer_walk=0,
        exit_elevator=timing["exit_elevator"],
    )
    before_board = breakdown.home_walk + breakdown.station_entry_elevator + breakdown.worst_case_wait
    total = before_board + breakdown.subway_ride + breakdown.transfer_walk + breakdown.exit_elevator

    be_estimate: Optional[dict] = None
    if arrive_hm:
        arrive_dt = now.replace(hour=arrive_hm[0], minute=arrive_hm[1])
        if arrive_dt < now:
            arrive_dt += timedelta(days=1)
        if is_possible and not same_station and ride_min > 0:
            # 데이터 소스(BE)가 역산하면 그 결과를 씁니다. 분 값은 모두 AI 값을 명시해서 보냅니다
            # (BE 기본값, 특히 여유 시간 0분에 맡기지 않음). Mock은 None → 아래에서 직접 계산.
            be_estimate = src.departure_estimate(
                arrive_dt.strftime("%Y-%m-%dT%H:%M:00") + KST_OFFSET,
                travel_minutes=ride_min,
                buffer_minutes=breakdown.worst_case_wait,
                walk_minutes=breakdown.home_walk,
                entry_minutes=breakdown.station_entry_elevator,
                exit_minutes=breakdown.exit_elevator,
            )
        if be_estimate is not None:
            leave_dt = _kst_naive(be_estimate["recommended_departure_at"])
            board_dt = _kst_naive(be_estimate["estimated_boarding_at"])
            arrive_dt = _kst_naive(be_estimate["arrival_at"])
        else:
            leave_dt = arrive_dt - timedelta(minutes=total)
            board_dt = leave_dt + timedelta(minutes=before_board)
    else:
        leave_dt = now.replace(hour=depart_hm[0], minute=depart_hm[1]) if depart_hm else now
        arrive_dt = leave_dt + timedelta(minutes=total)
        board_dt = leave_dt + timedelta(minutes=before_board)
    # 남은 시간·지각 판정은 AI의 기준 시각(now)으로 합니다. BE already_past는 BE 서버 시계 기준이라 쓰지 않습니다.
    minutes_from_now = int((leave_dt - now).total_seconds() // 60)

    if be_estimate is not None:
        time_limits = _be_time_limitations(be_estimate)
    else:
        time_limits = [LIMIT_ESTIMATED] if (is_possible and not same_station) else []

    return PlanTripOutput(
        is_possible=is_possible,
        departure_time=leave_dt.strftime("%H:%M"),
        board_time=board_dt.strftime("%H:%M"),
        arrival_time=arrive_dt.strftime("%H:%M"),
        minutes_from_now=minutes_from_now,
        # 지각 판정은 도착 목표가 있는 역산 모드에서만 의미가 있습니다.
        is_late=bool(arrive_hm) and minutes_from_now < 0,
        route=route,
        detours=detours,
        breakdown=breakdown,
        warnings=warnings,
        estimated=True,
        limitations=list(dict.fromkeys(time_limits + limitations)),
        time_source="BE" if be_estimate is not None else "AI",
    )


def _kst_naive(iso: str) -> datetime:
    """BE ISO 시각(오프셋 포함)을 KST 기준 naive datetime으로 바꿉니다 (plan_trip의 now와 같은 기준)."""
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError) as e:
        raise DataUnavailableError(f"BE 응답 해석 실패 (/api/departure-time): 시각 형식 {iso!r}") from e
    if dt.tzinfo is None:
        raise DataUnavailableError(f"BE 응답 해석 실패 (/api/departure-time): 오프셋 없는 시각 {iso!r}")
    return dt.astimezone(KST).replace(tzinfo=None)


def _be_time_limitations(estimate: dict) -> List[str]:
    """BE 역산 응답의 limitations 중 사용자에게 전할 것.

    빼는 것:
        - 엘리베이터 미확인 문구: BE가 역 상태를 받지 않아 붙는 문구. 역 상태는 plan_trip이 직접 확인해 따로 알립니다.
        - 이미 지났음 문구: BE 서버 시계 기준. 지각 여부는 AI 기준 시각으로 is_late에 담습니다.
    """
    drop = {BE_LIMIT_ELEVATOR_UNCONFIRMED, BE_LIMIT_ALREADY_PAST}
    message = (estimate.get("elevator_warning") or {}).get("message")
    if message:
        drop.add(message)
    return [x for x in (estimate.get("limitations") or []) if isinstance(x, str) and x not in drop]


def detour_text(d: dict) -> str:
    """대체역 한 줄 설명 (응답 빌더·폴백 엔진 공용). d는 DetourInfo의 dict.

    확인된 정보만 씁니다: 등록된 안내 문구 > 정거장 수·노선 공유 > 이름만.
    """
    name = d["alternative_station_name"]
    if d.get("bus_tip"):
        return f"{name}역 ({d['bus_tip']})"
    details = []
    if d.get("stops_away"):
        details.append(f"{d['stops_away']}정거장 거리")
    if d.get("same_line"):
        details.append("같은 노선")
    if d.get("alternative_status") in ("AVAILABLE", "PARTIAL"):
        details.append("엘리베이터 가동 확인")
    return f"{name}역 ({', '.join(details)})" if details else f"{name}역"
