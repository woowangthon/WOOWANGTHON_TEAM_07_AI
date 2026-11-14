"""이동 중 대체역 문의를 위한 명시적 슬롯 수집. 역 순서로 접근성을 추측하지 않습니다."""

import re

from ..data import get_data_source, station_name
from ..models import ActionPayload, ChatResponse, ChatResponseMetadata, ToolCallRecord
from .station import resolve_station
from .elevator import describe_status, get_elevator_status
from .trip import detour_text, plan_trip


def directional_response(message, session_id, context):
    """context['travel']만 갱신합니다. 일반 질의에는 None을 돌려줍니다.

    라벨을 붙인 정보만 수집하며 최근 조회역을 현재 위치로 취급하지 않습니다.
    완성된 슬롯은 한 번의 분석에만 사용하고 다음 분석에는 재확인합니다.
    """
    onboard_words = re.search(r"타고\s*있|승차\s*중|이동\s*중|열차\s*안|타고.*가는\s*중", message)
    trigger = onboard_words or (re.search(r"방면|반대\s*방향|진행\s*방향", message) and re.search(r"대체|고장|어디|내려|갈아|우회", message))
    travel = context.get("travel")
    if not trigger and "travel" not in context:
        return None
    # 명시적인 새 질의는 수집 대화를 종료한다. 일반 상태 조회를 무한히 가로채지 않는다.
    if not trigger and (re.search(r"새\s*질문|다른\s*질문|고장난\s*역", message) or (not re.search(r"현재|지금|목적지|도착역|다음|고장\s*역|호선|탑승\s*전|승차\s*전|취소|그만|새로", message) and re.search(r"엘리베이터|상태|날씨|안녕", message))):
        context.pop("travel", None)
        return None
    travel = dict(travel or {})
    if re.search(r"취소|그만|새로", message):
        context.pop("travel", None)
        return ChatResponse(session_id=session_id, text="이동 방면 확인을 초기화했습니다. 어떤 역을 도와드릴까요?", card_type="help")
    patterns = {
        "current": r"(?:현재역|현재\s+역(?=\s|[:：])|현재|지금)\s*[:：]?\s*([가-힣0-9]+)",
        "destination": r"(?:목적지|도착역)\s*[:：]?\s*([가-힣0-9]+)",
        "next": r"(?:다음(?:\s*정차)?역|다음\s*정차)\s*[:：]?\s*([가-힣0-9]+)",
        "broken": r"고장\s*역\s*[:：]?\s*([가-힣0-9]+)",
    }
    invalid = []
    for key, pattern in patterns.items():
        match = re.search(pattern, message)
        if match:
            result = resolve_station(match.group(1))
            if result.coverage == "SUPPORTED":
                travel[key] = result.station_id
            else:
                travel.pop(key, None)
                invalid.append(match.group(1) + "역")
    line = re.search(r"(?<!\d)(\d+)\s*호선", message)
    if line:
        if line.group(1) in "12345678" and len(line.group(1)) == 1:
            travel["line"] = line.group(1)
        else:
            travel.pop("line", None)
            invalid.append(line.group(0))
    if re.search(r"탑승\s*전|승차\s*전|타기\s*전|아직\s*안\s*탔", message):
        travel["onboard"] = False
    elif re.search(r"타고\s*있|승차\s*중|열차\s*안|타고.*가는\s*중", message):
        travel["onboard"] = True
    context["travel"] = travel
    required = {"current": "현재 역", "line": "현재 또는 탈 노선", "destination": "목적지 역", "broken": "고장 역", "next": "진행 방면의 다음 정차역", "onboard": "탑승 전인지 이미 타고 있는지"}
    missing = [label for key, label in required.items() if key not in travel]
    if missing:
        text = ("이동 방면을 임의로 정해 대체역을 안내하지 않겠습니다. " + ", ".join(missing) + "를 알려주세요.\n"
                "예: 현재 강남역, 2호선, 목적지 잠실역, 고장 역 잠실역, 다음 정차역 역삼역, 이미 타고 있어요. "
                "열차에 있다면 현재 역은 가장 최근 정차역으로 알려주세요. 이 예시는 입력 형식이며 이동 경로 추천이 아닙니다.")
        if invalid:
            text += "\n확인하지 못한 역: " + ", ".join(invalid)
        return ChatResponse(session_id=session_id, text=text, card_type="need_travel_context", metadata=ChatResponseMetadata(engine="fallback"))

    # 기존 계획 경로는 현재 열차의 방향을 증명하지 않으므로 대체역 후보만 조회합니다.
    src = get_data_source()
    if travel["current"] == travel["next"] or any(
        src.station(travel[key]).get("lines") and travel["line"] not in src.station(travel[key])["lines"]
        for key in ("current", "next")
    ):
        travel.pop("next", None)
        return ChatResponse(session_id=session_id, text="현재 역·노선·다음 정차역 정보가 일치하지 않습니다. 다음 정차역과 노선을 다시 확인해 알려주세요. 인접 여부와 실제 열차 진행 방향은 아직 검증되지 않았습니다.", card_type="need_travel_context")
    sid = travel["broken"]
    elevator = get_elevator_status(sid)
    plan = plan_trip(sid, sid)
    status = "승차 중" if travel["onboard"] else "탑승 전"
    lines = [f"알려주신 이동 정보: {station_name(travel['current'])}역, {travel['line']}호선, 다음 정차역 {station_name(travel['next'])}역, 목적지 {station_name(travel['destination'])}역, 고장 역 {station_name(sid)}역, {status}.",
             "현재 BE 정보에는 이 열차의 진행 방향과 대체역별 승강장 연결을 검증하는 계약이 없어, 후보가 앞인지 뒤인지 또는 같은 열차로 갈 수 있는지 확정할 수 없습니다."]
    lines.append(describe_status(elevator.model_dump()))
    if plan.detours:
        lines.append("고장 문의 역의 엘리베이터 이용 불가에 따른 조회 후보: " + ", ".join(detour_text(d.model_dump()) for d in plan.detours[:3]) + ".")
    else:
        lines.append("고장 문의 역에서 대체역 후보를 확인하지 못했습니다. 이는 그 역의 모든 이동 동선이 이용 가능하다는 뜻은 아닙니다.")
    lines += ["진행 방향 앞쪽 후보가 같은 승강장에서 하차 가능한지 확인되면 계속 탑승하는 선택을 검토할 수 있습니다. 뒤쪽 후보라면 반대편 승강장까지 계단 없는 이동·재탑승 가능 여부를 먼저 확인해야 합니다. 지금 내려서 이동하거나 반대 방향으로 갈아타라고 안내할 근거는 없습니다.",
              "환승역 고장이나 현재 역에서 내리는 문제라면 고장 역과 필요한 동선(승강장↔지상 또는 환승)을 따로 알려주세요. 역 직원에게 현장 동선 확인을 요청해 주세요."]
    lines += plan.warnings + list(dict.fromkeys(elevator.limitations + plan.limitations))
    context.pop("travel", None)
    return ChatResponse(session_id=session_id, text="\n".join(lines), card_type="directional_alternatives",
                        actions=[ActionPayload(type="openStation", label=f"{d.alternative_station_name}역 후보 정보", payload={"station_id": d.alternative_station_id}) for d in plan.detours[:3]],
                        tool_calls=[ToolCallRecord(name="get_elevator_status", args={"station_id": sid}), ToolCallRecord(name="plan_trip", args={"start_id": sid, "end_id": sid})],
                        metadata=ChatResponseMetadata(engine="fallback", data_updated_at=elevator.updated_at))
