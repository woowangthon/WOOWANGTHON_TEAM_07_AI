"""도구 실행 결과로 카드 종류·액션·추천 질문·안전 문장을 만듭니다.

LLM은 안내 문장(text)만 씁니다. 화면에 쓰는 card_type, actions는 이 모듈이 도구 결과로 직접 조립합니다.
safe_text는 도구 결과만으로 만든 템플릿 문장으로, LLM 문장이 검증(guard)에 걸리거나 비어 있을 때 대신 씁니다.
card_type 이름은 폴백 엔진과 같습니다 (IMPLEMENTATION_STATUS §3.4).
"""

from dataclasses import dataclass, field
from typing import List, Optional

from ..data import station_name
from ..models import ActionPayload

DEFAULT_SUGGESTIONS = ["강남역 엘리베이터 돼?", "신도림에서 잠실까지 3시까지 가려면?", "지금 고장난 역 알려줘"]

HELP_TEXT = (
    "안녕하세요! 지하철 엘리베이터 가동 현황과 휠체어 대체 우회 경로, 그리고 출발 시각을 안내하는 '우왕이'입니다.\n"
    "어떤 역을 도와드릴까요?"
)


@dataclass
class ToolResult:
    """실제로 실행한 도구 호출 하나. result는 execute_tool의 반환값 (오류면 {"error": ...})."""
    name: str
    args: dict
    result: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return "error" not in self.result


@dataclass
class BuiltResponse:
    card_type: str
    actions: List[ActionPayload]
    suggestions: List[str]
    safe_text: str
    data_updated_at: Optional[str] = None


def _last(results: List[ToolResult], name: str, pred=lambda r: True) -> Optional[ToolResult]:
    for r in reversed(results):
        if r.name == name and r.ok and pred(r):
            return r
    return None


def _is_route(r: ToolResult) -> bool:
    return r.args.get("start_id") != r.args.get("end_id")


def _detour_actions(detours: List[dict]) -> List[ActionPayload]:
    return [
        ActionPayload(
            type="showAlternative", label=f"{d['alternative_station_name']}역 우회 경로 보기",
            payload={"from": d["broken_station_id"], "to": d["alternative_station_id"],
                     "path": [d["broken_station_id"], d["alternative_station_id"]]},
        )
        for d in detours
    ]


def _trip(r: ToolResult) -> BuiltResponse:
    plan, args = r.result, r.args
    origin_id, dest_id, arrive_by = args.get("start_id"), args.get("end_id"), args.get("arrive_by")
    origin, dest = station_name(origin_id), station_name(dest_id)
    suggestions = [f"{dest}역 엘리베이터 상태는?", "지금 고장난 역 알려줘"]

    if not plan["is_possible"]:
        reason = plan["warnings"][0] if plan["warnings"] else f"{origin}역→{dest}역 경로는 계산할 수 없습니다."
        return BuiltResponse("trip_unsupported", [], suggestions, reason)

    if arrive_by:
        lines = [
            f"{origin}역에서 {dest}역까지 {arrive_by}까지 도착하시려면 {plan['departure_time']}에 출발하세요. "
            f"({plan['board_time']} 열차 탑승, {plan['arrival_time']} 도착 예정)"
        ]
        if plan["is_late"]:
            lines.append("지금 바로 출발해도 도착 희망 시각보다 늦을 수 있어요.")
    else:
        lines = [
            f"{origin}역에서 {dest}역으로 {plan['departure_time']}에 출발하시면 {plan['arrival_time']}경 도착합니다. "
            f"(열차 {plan['route']['total_stops']}개 역, 엘리베이터·대기 여유 포함)"
        ]
    lines += [f"⚠️ {w}" for w in plan["warnings"]]

    actions = [ActionPayload(
        type="openTime", label="출발 계산 탭에서 열기",
        payload={"originId": origin_id, "destId": dest_id, "arriveBy": arrive_by},
    )]
    actions += _detour_actions(plan["detours"])
    return BuiltResponse("trip_plan", actions, suggestions, "\n".join(lines))


def _elevator(r: ToolResult, results: List[ToolResult]) -> BuiltResponse:
    status = r.result
    sid, name = status["station_id"], status["station_name"]
    actions = [ActionPayload(type="openStation", label=f"{name}역 상세 보기", payload={"station_id": sid})]
    suggestions = [f"{name}역까지 가려면 언제 출발해?", "지금 고장난 역 알려줘"]

    overall = status["overall_status"]
    if overall == "AVAILABLE":
        text = f"현재 {name}역의 모든 엘리베이터는 정상 운행 중입니다."
    elif overall in ("PARTIAL", "UNAVAILABLE"):
        broken = [s for s in status["segments"] if s["status"] != "AVAILABLE"]
        where = ", ".join(f"{s['location']}({s['floors']})" for s in broken)
        scope = "전체" if overall == "UNAVAILABLE" else "일부"
        text = f"현재 {name}역 엘리베이터 {scope} 구간을 이용할 수 없습니다: {where}."
        alt = _last(results, "plan_trip",
                    lambda x: x.args.get("start_id") == sid and x.args.get("end_id") == sid)
        if alt and alt.result["detours"]:
            d = alt.result["detours"][0]
            text += f"\n추천 대체역: {d['alternative_station_name']}역 ({d['bus_tip']})"
            actions = _detour_actions(alt.result["detours"][:1]) + actions
    else:
        text = f"{name}역의 엘리베이터 정보를 확인할 수 없습니다."
    return BuiltResponse("elevator_status", actions, suggestions, text, status.get("updated_at"))


def _alternative_only(r: ToolResult) -> BuiltResponse:
    sid = r.args.get("start_id")
    name = station_name(sid)
    detours = r.result["detours"]
    if detours:
        d = detours[0]
        text = f"{name}역 대체역으로 {d['alternative_station_name']}역을 추천합니다. ({d['bus_tip']})"
    else:
        text = f"{name}역의 대체역 정보를 찾지 못했습니다."
    actions = _detour_actions(detours[:1]) + [
        ActionPayload(type="openStation", label=f"{name}역 상세 보기", payload={"station_id": sid})
    ]
    return BuiltResponse("elevator_status", actions, [f"{name}역 엘리베이터 상태는?"], text)


def _outages(r: ToolResult) -> BuiltResponse:
    res = r.result
    outages = res["outages"]
    if not outages:
        return BuiltResponse(
            "outages_empty", [], ["강남역 엘리베이터 돼?"],
            "현재 엘리베이터 고장이 확인된 역이 없습니다.", res.get("updated_at"),
        )
    names = ", ".join(f"{o['station_name']}역" for o in outages[:3])
    more = " 등" if res["count"] > 3 else ""
    actions = [
        ActionPayload(type="openStation", label=f"{o['station_name']}역 상세 보기",
                      payload={"station_id": o["station_id"]})
        for o in outages[:3]
    ]
    return BuiltResponse(
        "outages_list", actions, [f"{outages[0]['station_name']}역 대체역은?", "강남역 엘리베이터 돼?"],
        f"현재 엘리베이터 이용이 어려운 역은 총 {res['count']}곳입니다: {names}{more}.",
        res.get("updated_at"),
    )


def build_response(results: List[ToolResult]) -> BuiltResponse:
    """도구 결과 목록으로 카드·액션·안전 문장을 만듭니다.

    우선순위: 경로(plan_trip, 출발≠도착) > 엘리베이터 상태 > 대체역만(plan_trip, 출발=도착)
            > 고장 목록 > 지원 범위 밖 역 > 찾을 수 없는 역 > 도움말
    """
    trip = _last(results, "plan_trip", _is_route)
    if trip:
        return _trip(trip)

    status = _last(results, "get_elevator_status")
    if status:
        return _elevator(status, results)

    alt = _last(results, "plan_trip", lambda r: not _is_route(r))
    if alt:
        return _alternative_only(alt)

    outages = _last(results, "get_broken_elevators")
    if outages:
        return _outages(outages)

    out = _last(results, "resolve_station", lambda r: r.result.get("coverage") == "OUT_OF_COVERAGE")
    if out:
        return BuiltResponse(
            "out_of_coverage", [], DEFAULT_SUGGESTIONS,
            f"죄송해요. '{out.result['name']}'은(는) 아직 지원하지 않는 지역/노선이에요. "
            "현재는 수도권 지하철 1~8호선 역만 안내할 수 있어요.",
        )

    missing = _last(results, "resolve_station", lambda r: r.result.get("coverage") == "NOT_FOUND")
    if missing:
        return BuiltResponse(
            "need_station", [], DEFAULT_SUGGESTIONS,
            f"'{missing.args.get('query', '')}'은(는) 찾을 수 없는 역이에요. 역 이름을 다시 확인해 주세요.",
        )

    return BuiltResponse("help", [], DEFAULT_SUGGESTIONS, HELP_TEXT)
