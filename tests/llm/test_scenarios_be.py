"""/api/chat 통합 시나리오 테스트 — BE 데이터 소스 (AI 이슈 #12, BE 이슈 #42와 같은 5개 시나리오).

실제 ChatOrchestrator를 BeDataSource에 붙이고, BE 서버는 httpx.MockTransport로 흉내 낸다.
BE 응답은 데모 DB 서버의 실제 응답 픽스처(tests/data/fixtures/be_demo_responses.json, 읽기만 함)와
출발 시각 API 실제 응답(tests/llm/fixtures/scenario_be_departure_time.json)이다.

경로마다 두 가지로 확인한다.
    - 폴백 경로: LLM이 응답하지 못함(timeout) → 규칙 기반 폴백 엔진이 BE 데이터로 답함
    - LLM 경로: FakeLLM이 BE 역 ID로 도구를 호출하는 대본을 따름 → 카드·액션·검증·limitations 강제

시나리오: 1 엘리베이터 상태, 2 대체역, 3 출발 시각, 4 고장·점검 목록, 5 경로 조회(BE #37 대기, skip),
그리고 BE 장애·지원 범위 밖 역.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fake_llm import FakeLLM, call, say, tools, unavailable

from AI.src.data import use_data_source
from AI.src.data.be_source import BeDataSource
from AI.src.llm.orchestrator import ChatOrchestrator
from AI.src.llm.session import SessionStore
from AI.src.models import ChatRequest
from AI.src.tools.elevator import LIMIT_SOME_UNKNOWN

TESTS = Path(__file__).resolve().parents[1]
FIX = json.loads((TESTS / "data" / "fixtures" / "be_demo_responses.json").read_text(encoding="utf-8"))
DEPARTURE_SAMPLE = json.loads(
    (Path(__file__).parent / "fixtures" / "scenario_be_departure_time.json").read_text(encoding="utf-8"))
NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "요청한 리소스를 찾을 수 없습니다."}}

# BE 데모 DB의 역 ID
GANGNAM, SADANG, SINDORIM, JAMSIL, SEOLLEUNG, YEOKSAM = "31", "35", "43", "25", "29", "30"

ALT_LIMITATION = FIX["alternatives"][GANGNAM]["limitations"][0]          # 대체역 경로 미검증
OUTAGES_LIMITATION = FIX["elevators_status"]["limitations"][0]           # 일부 설비 미확인
SADANG_LIMITATION = FIX["elevators"][SADANG]["limitations"][0]           # 판정할 수 없음


# ---------------------------------------------------------------------------
# 가짜 BE
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))
DEP_SEGMENT_DEFAULTS = {"walk_to_station_minutes": 7, "station_entry_minutes": 3, "exit_walk_minutes": 3}
DEP_RANGES = {"travel_minutes": (1, 720), "buffer_minutes": (0, 240),
              **{k: (0, 120) for k in DEP_SEGMENT_DEFAULTS}}
DEP_PLAN = (("WALK_TO_STATION", "집에서 역까지 도보", "walk_to_station_minutes"),
            ("STATION_ENTRY", "역 진입", "station_entry_minutes"),
            ("BUFFER", "여유 시간", "buffer_minutes"),
            ("RIDE", "열차 이동(외부 입력 예상 시간)", "travel_minutes"),
            ("EXIT_WALK", "출구 이동", "exit_walk_minutes"))
DEP_ELEVATOR_UNCONFIRMED = DEPARTURE_SAMPLE["response"]["elevator_warning"]["message"]
DEP_LIMITATIONS = DEPARTURE_SAMPLE["response"]["limitations"]  # 시간표 미사용·이동 시간 외부 입력·구간 추정·엘리베이터 미확인


def _validation_error(message: str) -> httpx.Response:
    return httpx.Response(422, json={"error": {"code": "VALIDATION_ERROR", "message": message}})


def _iso(t: datetime) -> str:
    return t.astimezone(KST).isoformat()


def departure_time(body) -> httpx.Response:
    """BE POST /api/departure-time 흉내 (BE docs/api-contract.md §8.2, app/routers/departure_time.py).

    권장 출발 = 도착 희망 − (집→역 도보 + 역 진입 + 여유 + 예상 이동 + 출구 이동).
    already_past는 BE 서버 시계로 정해지지만, 테스트가 실행 시각에 따라 달라지지 않도록 항상 false로 둔다.
    """
    if not isinstance(body, dict):
        return _validation_error("JSON 객체가 아닙니다.")
    allowed = {"arrival_at", "travel_minutes", "buffer_minutes", *DEP_SEGMENT_DEFAULTS}
    if set(body) - allowed:
        return _validation_error(f"알 수 없는 필드: {sorted(set(body) - allowed)}")
    arrival_raw = body.get("arrival_at")
    try:
        arrival = datetime.fromisoformat(arrival_raw) if isinstance(arrival_raw, str) else None
    except ValueError:
        arrival = None
    if arrival is None or arrival.tzinfo is None or arrival.microsecond:
        return _validation_error("도착 희망 시각은 오프셋이 있는 ISO 8601(초 단위) 문자열이어야 합니다.")

    inputs, defaults_applied = {}, []
    for key in ("travel_minutes", "buffer_minutes", *DEP_SEGMENT_DEFAULTS):
        value = body.get(key)
        if value is None:
            if key == "travel_minutes":
                return _validation_error("travel_minutes는 필수입니다.")
            if key == "buffer_minutes":
                value = 0  # 기본 0, defaults_applied에 넣지 않음 (BE와 같음)
            else:
                value = DEP_SEGMENT_DEFAULTS[key]
                defaults_applied.append(key)
        low, high = DEP_RANGES[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            return _validation_error(f"{key} 값이 올바르지 않습니다: {value!r}")
        inputs[key] = value

    total = sum(inputs[key] for _, _, key in DEP_PLAN)
    departure = arrival - timedelta(minutes=total)
    timeline, cursor = [], departure
    for kind, label, key in DEP_PLAN:
        end = cursor + timedelta(minutes=inputs[key])
        timeline.append({"kind": kind, "label": label, "start_at": _iso(cursor), "end_at": _iso(end),
                         "minutes": inputs[key]})
        cursor = end
    ride = next(s for s in timeline if s["kind"] == "RIDE")
    return httpx.Response(200, json={
        "recommended_departure_at": _iso(departure),
        "estimated_boarding_at": ride["start_at"],
        "estimated_alighting_at": ride["end_at"],
        "arrival_at": _iso(arrival),
        "total_minutes": total,
        "date_changed": departure.astimezone(KST).date() != arrival.astimezone(KST).date(),
        "already_past": False,
        "estimated": True,
        "timetable_used": False,
        "inputs": inputs,
        "defaults_applied": defaults_applied,
        "timeline": timeline,
        "elevator_warning": {"status": "UNCONFIRMED", "items": [], "message": DEP_ELEVATOR_UNCONFIRMED},
        "limitations": list(DEP_LIMITATIONS),
    })


def fake_be(request: httpx.Request) -> httpx.Response:
    """BE 데모 DB 서버 흉내. 픽스처에 없는 역의 엘리베이터는 빈 목록, 없는 역은 404."""
    parts = request.url.path.strip("/").split("/")  # api, stations, {id}, ...
    if request.method == "POST":
        if parts == ["api", "departure-time"]:
            try:
                body = json.loads(request.content or b"null")
            except ValueError:
                return _validation_error("JSON 본문을 해석할 수 없습니다.")
            return departure_time(body)
        return httpx.Response(405, json={"error": {"code": "METHOD_NOT_ALLOWED", "message": "x"}})
    if parts == ["api", "elevators", "status"]:
        return httpx.Response(200, json=FIX["elevators_status"])
    if parts[:2] == ["api", "stations"] and len(parts) >= 3:
        sid = parts[2]
        if len(parts) == 3:
            body = FIX["stations"].get(sid)
        elif parts[3] == "elevators":
            body = FIX["elevators"].get(sid) or (
                {"station": {"station_id": int(sid)}, "station_status": {}, "limitations": [], "elevators": []}
                if sid in FIX["stations"] else None)
        elif parts[3] == "alternatives":
            body = FIX["alternatives"].get(sid)
        else:
            body = None
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404, json=NOT_FOUND)
    return httpx.Response(404, json=NOT_FOUND)


def be_down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def elevators_down(request: httpx.Request) -> httpx.Response:
    """역 목록은 읽히지만 엘리베이터 조회부터 BE가 실패하는 경우 (대화 도중 장애)."""
    if "elevators" in request.url.path:
        return httpx.Response(500, json={"error": {"code": "INTERNAL_ERROR", "message": "x"}})
    return fake_be(request)


def be_source(handler=fake_be) -> BeDataSource:
    return BeDataSource(http=httpx.Client(base_url="http://be.test", transport=httpx.MockTransport(handler)))


@pytest.fixture
def be():
    with use_data_source(be_source()) as src:
        yield src


def fallback_orch(**kw) -> ChatOrchestrator:
    """LLM이 매번 타임아웃 → 폴백 엔진이 답하는 오케스트레이터."""
    return ChatOrchestrator(client=FakeLLM([unavailable("timeout")] * 10), sessions=SessionStore(), **kw)


def llm_orch(script, **kw):
    llm = FakeLLM(script)
    return ChatOrchestrator(client=llm, sessions=SessionStore(), **kw), llm


def ask(orch, message, session_id="s1", current_time="12:00"):
    return orch.handle(ChatRequest(session_id=session_id, message=message, current_time=current_time))


def assert_fallback(res):
    assert (res.metadata.engine, res.metadata.fallback_used, res.metadata.fallback_reason) == \
        ("fallback", True, "timeout")


def assert_llm(res, guard_triggered=False):
    assert (res.metadata.engine, res.metadata.fallback_used, res.metadata.fallback_reason) == ("llm", False, None)
    assert res.metadata.guard_triggered is guard_triggered


def test_fake_departure_time_matches_recorded_be_response():
    """가짜 BE의 출발 시각 계산이 BE 실제 응답과 같은지 (공식·키·defaults_applied·limitations 순서)."""
    r = departure_time(DEPARTURE_SAMPLE["request"])
    assert r.status_code == 200
    assert r.json() == DEPARTURE_SAMPLE["response"]
    # 계약 공식: buffer를 명시하면 그만큼 출발이 당겨지고 defaults_applied에는 들어가지 않는다
    body = departure_time({**DEPARTURE_SAMPLE["request"], "buffer_minutes": 5}).json()
    assert body["recommended_departure_at"] == "2026-12-01T14:22:00+09:00"
    assert body["estimated_boarding_at"] == "2026-12-01T14:37:00+09:00"
    assert "buffer_minutes" not in body["defaults_applied"]
    assert departure_time({**DEPARTURE_SAMPLE["request"], "unknown": 1}).status_code == 422
    assert departure_time({"arrival_at": "2026-12-01T15:00:00", "travel_minutes": 20}).status_code == 422


# ---------------------------------------------------------------------------
# 시나리오 1. 엘리베이터 상태
# ---------------------------------------------------------------------------

def _assert_gangnam_status(res):
    """강남: BE 판정 UNAVAILABLE (5대 중 합성 1대 가동 안됨, 4대 STALE)."""
    assert res.card_type == "elevator_status"
    assert f"※ {LIMIT_SOME_UNKNOWN}" in res.text
    assert "정상" not in res.text
    assert res.actions[0].type == "showAlternative"
    assert res.actions[0].payload == {"from": GANGNAM, "to": YEOKSAM, "path": [GANGNAM, YEOKSAM]}
    assert {"type": "openStation", "payload": {"station_id": GANGNAM}} in \
        [{"type": a.type, "payload": a.payload} for a in res.actions]


def test_s1_elevator_status_fallback(be):
    res = ask(fallback_orch(), "강남역 엘리베이터 돼?")
    assert_fallback(res)
    _assert_gangnam_status(res)
    assert "5대 중 1대를 이용할 수 없는" in res.text and "4대는 상태를 확인하지 못했습니다" in res.text
    assert [(t.name, t.args) for t in res.tool_calls] == [
        ("resolve_station", {"query": "강남"}),
        ("get_elevator_status", {"station_id": GANGNAM}),
        ("plan_trip", {"start_id": GANGNAM, "end_id": GANGNAM}),
    ]
    assert "역삼역" in res.text and f"※ {ALT_LIMITATION}" in res.text


def test_s1_elevator_status_llm_limitation_is_appended(be):
    # LLM이 미확인 안내(※)를 빼고 요약 → 오케스트레이터가 BE limitations를 그대로 덧붙인다 (BE 계약 R9)
    orch, llm = llm_orch([
        tools(call("resolve_station", query="강남")),
        tools(call("get_elevator_status", station_id=GANGNAM)),
        say("강남역 엘리베이터 5대 중 1대를 이용할 수 없는 것으로 확인되고, 4대는 상태를 확인하지 못했어요."),
    ])
    res = ask(orch, "강남역 엘리베이터 돼?")
    assert_llm(res)
    assert res.text.startswith("강남역 엘리베이터 5대 중 1대")
    _assert_gangnam_status(res)
    # 도구 결과가 BE 역 ID로 LLM에 전달됨
    assert json.loads(llm.requests[1][-1]["content"])["station_id"] == GANGNAM
    status = json.loads(llm.requests[2][-1]["content"])  # LLM이 본 get_elevator_status 결과
    assert (status["overall_status"], status["total_count"], status["unknown_count"]) == ("UNAVAILABLE", 5, 4)
    assert status["limitations"] == [LIMIT_SOME_UNKNOWN]
    # 고장 역인데 대체역 조회가 빠져 코드가 BE 대체역을 보충 조회하고 한 줄 덧붙인다
    assert [t.name for t in res.tool_calls] == ["resolve_station", "get_elevator_status", "plan_trip"]
    assert "추천 대체역: 역삼역" in res.text and f"※ {ALT_LIMITATION}" in res.text


def test_s1_all_stale_station_fallback(be):
    res = ask(fallback_orch(), "사당역 엘리베이터 돼?")
    assert_fallback(res)
    assert res.card_type == "elevator_status"
    assert "확인할 수 없습니다" in res.text and "정상" not in res.text
    assert f"※ {SADANG_LIMITATION}" in res.text
    assert [a.type for a in res.actions] == ["openStation"]  # UNKNOWN이면 대체역을 고르지 않음
    assert res.actions[0].payload == {"station_id": SADANG}


def test_s1_all_stale_station_llm(be):
    orch, _ = llm_orch([
        tools(call("get_elevator_status", station_id=SADANG)),
        say("사당역 엘리베이터 상태는 지금 확인할 수 없어요. 역에 문의해 주세요."),
    ])
    res = ask(orch, "사당역 엘리베이터 돼?")
    assert_llm(res)
    assert res.card_type == "elevator_status"
    assert "확인할 수 없" in res.text and "정상" not in res.text
    assert f"※ {SADANG_LIMITATION}" in res.text
    assert [t.name for t in res.tool_calls] == ["get_elevator_status"]  # UNKNOWN은 대체역 보충 대상 아님


# ---------------------------------------------------------------------------
# 시나리오 2. 대체역 (세션 이어 가기)
# ---------------------------------------------------------------------------

def test_s2_alternative_follow_up_fallback(be):
    orch = fallback_orch()
    ask(orch, "강남역 엘리베이터 돼?")
    assert orch.sessions.get("s1").last_station_id == GANGNAM
    res = ask(orch, "거기 말고 다른 역은?")
    assert_fallback(res)
    assert res.card_type == "elevator_status"
    assert "강남역" in res.text and "역삼역" in res.text  # BE 추천 1순위
    assert res.actions[0].type == "showAlternative"
    assert res.actions[0].payload == {"from": GANGNAM, "to": YEOKSAM, "path": [GANGNAM, YEOKSAM]}
    assert f"※ {ALT_LIMITATION}" in res.text


def _gangnam_first_turn():
    return [
        tools(call("get_elevator_status", station_id=GANGNAM)),
        say("강남역 엘리베이터 5대 중 1대를 이용할 수 없어요."),
    ]


def test_s2_alternative_follow_up_llm(be):
    orch, llm = llm_orch(_gangnam_first_turn() + [
        tools(call("plan_trip", start_id=GANGNAM, end_id=GANGNAM)),
        say("강남역 대신 역삼역을 이용해 보세요. 교대역, 남부터미널역도 후보예요."),
    ])
    ask(orch, "강남역 엘리베이터 돼?")
    res = ask(orch, "거기 말고 다른 역은?")
    assert "강남 (31)" in llm.requests[-1][0]["content"]  # 세션의 최근 역이 BE ID로 프롬프트에 들어감
    assert_llm(res)
    assert res.card_type == "elevator_status"
    assert res.text.startswith("강남역 대신 역삼역")
    assert [a.type for a in res.actions] == ["showAlternative", "openStation"]
    assert res.actions[0].payload == {"from": GANGNAM, "to": YEOKSAM, "path": [GANGNAM, YEOKSAM]}
    assert f"※ {ALT_LIMITATION}" in res.text
    detours = json.loads(llm.requests[-1][-1]["content"])["detours"]
    assert [d["alternative_station_id"] for d in detours] == ["30", "32", "78"]  # BE 추천 순 그대로


def test_s2_fabricated_station_is_replaced_by_be_alternatives(be):
    # LLM이 BE 결과에 없는 역(논현)을 지어냄 → 검증이 막고 BE 대체역 순서로 만든 템플릿 문장으로 바꾼다
    orch, _ = llm_orch(_gangnam_first_turn() + [
        tools(call("plan_trip", start_id=GANGNAM, end_id=GANGNAM)),
        say("강남역 대신 논현역을 이용하세요."),
    ])
    ask(orch, "강남역 엘리베이터 돼?")
    res = ask(orch, "거기 말고 다른 역은?")
    assert_llm(res, guard_triggered=True)
    assert "논현역" not in res.text
    assert res.text.startswith("강남역 대체역으로 역삼역")
    i, j, k = (res.text.index(n) for n in ("역삼역", "교대역", "남부터미널역"))
    assert i < j < k
    assert res.actions[0].payload["to"] == YEOKSAM
    assert f"※ {ALT_LIMITATION}" in res.text


# ---------------------------------------------------------------------------
# 시나리오 3. 출발 시각
# 계산 방식(Mock 2호선 계산 → BE POST /api/departure-time)이 바뀌어도 통과하도록
# 시각·카드·액션과 "한계 안내가 있다"만 확인하고 문구는 고정하지 않는다.
# ---------------------------------------------------------------------------

TRIP_QUESTION = "신도림에서 잠실까지 3시까지 가려면?"
TRIP_PAYLOAD = {"originId": SINDORIM, "destId": JAMSIL, "arriveBy": "15:00"}


def _assert_trip(res):
    assert res.card_type == "trip_plan"
    assert res.actions[0].type == "openTime" and res.actions[0].payload == TRIP_PAYLOAD
    assert "\n※ " in res.text  # 추정 계산 등 한계 안내가 붙음


def test_s3_departure_time_fallback(be):
    res = ask(fallback_orch(), TRIP_QUESTION)
    assert_fallback(res)
    _assert_trip(res)
    assert "14:06에 출발하세요" in res.text.replace("**", "")
    assert "14:21 열차 탑승" in res.text and "15:00 도착 예정" in res.text
    assert res.tool_calls[-1].name == "plan_trip"
    assert res.tool_calls[-1].args == {"start_id": SINDORIM, "end_id": JAMSIL, "arrive_by": "15:00"}


def test_s3_departure_time_llm(be):
    orch, llm = llm_orch([
        tools(call("resolve_station", query="신도림"), call("resolve_station", query="잠실")),
        tools(call("plan_trip", start_id=SINDORIM, end_id=JAMSIL, arrive_by="15:00")),
        say("신도림역에서 14:06에 출발하시면 15:00까지 잠실역에 도착할 수 있어요."),
    ])
    res = ask(orch, TRIP_QUESTION)
    assert_llm(res)
    _assert_trip(res)
    assert res.text.startswith("신도림역에서 14:06에 출발")
    plan = json.loads(llm.requests[2][-1]["content"])
    assert (plan["departure_time"], plan["board_time"], plan["arrival_time"]) == ("14:06", "14:21", "15:00")
    assert plan["estimated"] is True and plan["limitations"]


def test_s3_departure_time_preresolve_template(be):
    # 응답 속도 옵션: 역 사전 확인이 BE ID를 프롬프트에 넣고, 템플릿 문장으로 답한다
    orch, llm = llm_orch([tools(call("plan_trip", start_id=SINDORIM, end_id=JAMSIL, arrive_by="15:00"))],
                         preresolve=True, answer_mode="template")
    res = ask(orch, TRIP_QUESTION)
    prompt = llm.requests[0][0]["content"]
    assert f"station_id={SINDORIM}" in prompt and f"station_id={JAMSIL}" in prompt
    assert len(llm.requests) == 1
    assert_llm(res)
    _assert_trip(res)
    assert "14:06에 출발하세요" in res.text


def test_s3_made_up_time_is_replaced(be):
    orch, _ = llm_orch([
        tools(call("plan_trip", start_id=SINDORIM, end_id=JAMSIL, arrive_by="15:00")),
        say("14:30에 출발하시면 됩니다."),
    ])
    res = ask(orch, TRIP_QUESTION)
    assert_llm(res, guard_triggered=True)
    _assert_trip(res)
    assert "14:06" in res.text and "14:30" not in res.text


# ---------------------------------------------------------------------------
# 시나리오 4. 고장·점검 목록
# ---------------------------------------------------------------------------

OUTAGE_QUESTIONS = [("지금 고장난 역 알려줘", {}),
                    ("2호선에서 엘리베이터 고장 난 역 목록 보여줘", {"line_id": "2"})]


def _assert_outages(res):
    assert res.card_type == "outages_list"
    assert "강남역" in res.text and "선릉역" in res.text
    assert [a.payload for a in res.actions] == [{"station_id": GANGNAM}, {"station_id": SEOLLEUNG}]
    assert f"※ {OUTAGES_LIMITATION}" in res.text


@pytest.mark.parametrize("message,args", OUTAGE_QUESTIONS)
def test_s4_outages_fallback(be, message, args):
    res = ask(fallback_orch(), message)
    assert_fallback(res)
    _assert_outages(res)
    assert [(t.name, t.args) for t in res.tool_calls] == [("get_broken_elevators", args)]


@pytest.mark.parametrize("message,args", OUTAGE_QUESTIONS)
def test_s4_outages_llm_limitation_is_appended(be, message, args):
    orch, _ = llm_orch([
        tools(call("get_broken_elevators", **args)),
        say("지금 엘리베이터를 이용하기 어려운 역은 강남역과 선릉역이에요."),
    ])
    res = ask(orch, message)
    assert_llm(res)
    assert res.text.startswith("지금 엘리베이터를 이용하기 어려운 역은")
    _assert_outages(res)


# ---------------------------------------------------------------------------
# 시나리오 5. 경로 조회 — BE 경로 API(#37) 대기
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="BE 경로 API(#37)가 아직 없음. 지금 경로는 Mock 2호선 계산(추정)이라 BE 경로로 검증할 수 없다")
def test_s5_route_from_be_route_api(be):
    """BE #37이 생기면 기대하는 동작 (구현 시 fake_be에 경로 API 응답을 추가하고 skip을 뗀다).

    - "신도림에서 잠실까지 가는 길 알려줘" → card trip_plan, plan_trip(start_id="43", end_id="25")
    - 경로·소요 시간은 BE 경로 API 응답 그대로 (AI가 Mock 2호선 계산을 하지 않음)
    - 경로의 역 ID는 BE ID, 경로 안 엘리베이터 미확인·고장 역은 limitations/warnings로 안내
    - 2호선 밖 구간(예: 고속터미널 77)도 BE가 경로를 주면 is_possible=True
    - BE가 경로를 못 찾으면 "계산할 수 없음"(trip_unsupported)으로 답하고 추정 경로를 만들지 않음
    """
    raise NotImplementedError("BE 경로 API(#37) 연동 후 작성")


# ---------------------------------------------------------------------------
# BE 장애·지원 범위 밖 역
# ---------------------------------------------------------------------------

def _assert_unavailable(res):
    assert res.card_type == "data_unavailable"
    assert (res.metadata.engine, res.metadata.fallback_reason) == ("fallback", "data_unavailable")
    assert "확인할 수 없습니다" in res.text and "정상" not in res.text
    assert res.actions == []


@pytest.mark.parametrize("message", ["강남역 엘리베이터 돼?", "지금 고장난 역 알려줘", TRIP_QUESTION])
def test_be_down_fallback(message):
    with use_data_source(be_source(be_down)):
        res = ask(fallback_orch(), message)
    _assert_unavailable(res)


def test_be_down_llm():
    orch, _ = llm_orch([
        tools(call("resolve_station", query="강남")),
        say("강남역 엘리베이터는 정상이에요."),  # 근거 없이 지어낸 답은 쓰지 않는다
    ])
    with use_data_source(be_source(be_down)):
        res = ask(orch, "강남역 엘리베이터 돼?")
    _assert_unavailable(res)


def test_be_down_llm_with_preresolve():
    orch, llm = llm_orch([say("강남역 엘리베이터는 정상이에요.")], preresolve=True)
    with use_data_source(be_source(be_down)):
        res = ask(orch, "강남역 엘리베이터 돼?")
    _assert_unavailable(res)
    assert llm.requests == []  # 역 사전 확인에서 BE 장애를 알아 LLM을 부르지 않음


def test_be_fails_mid_conversation_llm():
    # 역 목록은 읽혔지만 엘리베이터 조회가 5xx → 이전 값·LLM 문장으로 답하지 않는다
    orch, _ = llm_orch([
        tools(call("get_elevator_status", station_id=GANGNAM)),
        say("강남역 엘리베이터는 모두 가동 중이에요."),
    ])
    with use_data_source(be_source(elevators_down)):
        res = ask(orch, "강남역 엘리베이터 돼?")
    _assert_unavailable(res)


def test_be_fails_mid_conversation_fallback():
    with use_data_source(be_source(elevators_down)):
        res = ask(fallback_orch(), "강남역 엘리베이터 돼?")
    _assert_unavailable(res)


def test_out_of_coverage_fallback(be):
    res = ask(fallback_orch(), "판교역 엘리베이터 돼?")
    assert_fallback(res)
    assert res.card_type == "out_of_coverage"
    assert "판교" in res.text and res.actions == []
    assert [t.name for t in res.tool_calls] == ["resolve_station"]  # 다른 역으로 대신 답하지 않음


def test_out_of_coverage_llm(be):
    orch, llm = llm_orch([
        tools(call("resolve_station", query="판교")),
        say("죄송해요, 판교역은 아직 지원하지 않는 역이에요."),
    ])
    res = ask(orch, "판교역 엘리베이터 돼?")
    assert_llm(res)
    assert res.card_type == "out_of_coverage"
    assert json.loads(llm.requests[1][-1]["content"])["coverage"] == "OUT_OF_COVERAGE"
    assert res.actions == []
