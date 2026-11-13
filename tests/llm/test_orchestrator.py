"""ChatOrchestrator 테스트: 도구 호출 루프, 액션 조립, 폴백 전환, 가드, 세션 (src/llm/orchestrator.py)."""

import pytest
from fake_llm import FakeLLM, call, say, tools, unavailable

from AI.src.llm.orchestrator import ChatOrchestrator
from AI.src.llm.session import SessionStore
from AI.src.models import ChatRequest


def make(script, **kw):
    llm = FakeLLM(script)
    return ChatOrchestrator(client=llm, sessions=SessionStore(), **kw), llm


def ask(orch, message, session_id="s1", current_time="12:00"):
    return orch.handle(ChatRequest(session_id=session_id, message=message, current_time=current_time))


def test_tool_loop_builds_actions_from_tool_results():
    orch, llm = make([
        tools(call("resolve_station", query="사당")),
        tools(call("get_elevator_status", station_id="sadang"),
              call("plan_trip", start_id="sadang", end_id="sadang")),
        say("사당역 2호선 승강장 엘리베이터를 이용할 수 없어요. 방배역을 이용해 주세요."),
    ])
    res = ask(orch, "사당역 엘리베이터 돼?")

    assert res.metadata.engine == "llm"
    assert res.metadata.fallback_used is False
    assert res.metadata.guard_triggered is False
    assert res.text.startswith("사당역 2호선")
    assert res.card_type == "elevator_status"
    assert [a.type for a in res.actions] == ["showAlternative", "openStation"]
    assert res.actions[0].payload == {"from": "sadang", "to": "bangbae", "path": ["sadang", "bangbae"]}
    assert [t.name for t in res.tool_calls] == ["resolve_station", "get_elevator_status", "plan_trip"]
    # 도구 결과가 다음 LLM 요청에 tool 메시지로 들어갑니다.
    assert llm.requests[1][-1]["role"] == "tool"


def test_trip_uses_request_current_time():
    orch, _ = make([
        tools(call("plan_trip", start_id="sindorim", end_id="gangnam", arrive_by="15:00")),
        say("신도림역에서 14:18에 출발하시면 15:00까지 강남역에 도착해요."),
    ])
    res = ask(orch, "신도림에서 강남까지 3시까지 가려면?", current_time="12:00")
    assert res.card_type == "trip_plan"
    assert res.actions[0].type == "openTime"
    assert res.actions[0].payload == {"originId": "sindorim", "destId": "gangnam", "arriveBy": "15:00"}
    assert res.metadata.guard_triggered is False


@pytest.mark.parametrize("kind", ["timeout", "rate_limit", "network", "server_error", "not_configured"])
def test_llm_unavailable_switches_to_fallback(kind):
    orch, _ = make([unavailable(kind)])
    res = ask(orch, "사당역 엘리베이터 돼?")
    assert res.metadata.engine == "fallback"
    assert res.metadata.fallback_used is True
    assert res.metadata.fallback_reason == kind
    assert res.card_type == "elevator_status"
    assert "사당역" in res.text


def test_fallback_when_llm_keeps_calling_tools():
    orch, _ = make([tools(call("resolve_station", query="강남"))] * 2, max_rounds=2)
    res = ask(orch, "강남역 엘리베이터 돼?")
    assert res.metadata.fallback_reason == "max_rounds"


def test_fallback_when_llm_returns_empty_text():
    orch, _ = make([say("   ")])
    res = ask(orch, "강남역 엘리베이터 돼?")
    assert res.metadata.fallback_reason == "empty_reply"


def test_fallback_on_unexpected_error():
    orch, _ = make([RuntimeError("boom")])
    res = ask(orch, "강남역 엘리베이터 돼?")
    assert res.metadata.fallback_reason == "internal_error"


def test_guard_replaces_text_with_made_up_time():
    orch, _ = make([
        tools(call("plan_trip", start_id="sindorim", end_id="gangnam", arrive_by="15:00")),
        say("14:30에 출발하시면 됩니다."),  # 도구 결과는 14:18
    ])
    res = ask(orch, "신도림에서 강남까지 3시까지 가려면?")
    assert res.metadata.guard_triggered is True
    assert "14:18" in res.text and "14:30" not in res.text


def test_guard_replaces_text_with_made_up_station():
    orch, _ = make([
        tools(call("get_elevator_status", station_id="gangnam")),
        say("강남역은 정상이고, 근처 논현역도 이용 가능해요."),
    ])
    res = ask(orch, "강남역 엘리베이터 돼?")
    assert res.metadata.guard_triggered is True
    assert "논현역" not in res.text


def test_session_slot_is_used_in_next_prompt():
    orch, llm = make([
        tools(call("get_elevator_status", station_id="sadang")),
        say("사당역 일부 엘리베이터를 이용할 수 없어요."),
        say("어느 역이 궁금하세요?"),
    ])
    ask(orch, "사당역 엘리베이터 돼?")
    ask(orch, "거기 대체역은?")
    system_prompt = llm.requests[-1][0]["content"]
    assert "사당 (sadang)" in system_prompt


def test_fallback_reads_session_slot():
    orch, _ = make([
        tools(call("get_elevator_status", station_id="sadang")),
        say("사당역 일부 엘리베이터를 이용할 수 없어요."),
        unavailable("timeout"),
    ])
    ask(orch, "사당역 엘리베이터 돼?")
    res = ask(orch, "거기 대체역은?")
    assert res.metadata.engine == "fallback"
    assert "사당역" in res.text and "방배역" in res.text


def test_sessions_are_separate():
    orch, _ = make([unavailable("timeout")] * 2)
    ask(orch, "사당역 엘리베이터 돼?", session_id="a")
    ask(orch, "강남역 엘리베이터 돼?", session_id="b")
    assert orch.sessions.get("a").last_station_id == "sadang"
    assert orch.sessions.get("b").last_station_id == "gangnam"
