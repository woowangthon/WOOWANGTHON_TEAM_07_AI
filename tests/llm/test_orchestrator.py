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


def test_guard_rejection_without_tools_falls_back():
    orch, _ = make([
        tools(call("get_elevator_status", station_id="sadang")),
        say("사당역 일부 엘리베이터를 이용할 수 없어요."),
        say("강남역 대체역은 논현역이에요."),  # 도구 없이 지어낸 답
    ])
    ask(orch, "사당역 엘리베이터 돼?")
    res = ask(orch, "거기 대체역은?")
    assert res.metadata.engine == "fallback"
    assert res.metadata.fallback_reason == "guard_rejected"
    assert "방배역" in res.text and "논현역" not in res.text


def test_missing_alternative_is_supplemented_for_broken_station():
    # 벤치마크 CX08: 고장 역 상태만 조회하고 대체역은 부르지 않은 경우
    orch, _ = make([
        tools(call("get_elevator_status", station_id="sillim")),
        say("신림역 6번 출구 엘리베이터를 이용할 수 없어요."),
    ])
    res = ask(orch, "신림역 엘리베이터 돼?")
    assert res.metadata.guard_triggered is False
    assert [t.name for t in res.tool_calls] == ["get_elevator_status", "plan_trip"]
    assert res.tool_calls[1].args == {"start_id": "sillim", "end_id": "sillim"}
    assert res.text.startswith("신림역 6번 출구")
    assert "추천 대체역: 신대방역" in res.text
    assert res.actions[0].type == "showAlternative"
    assert res.actions[0].payload["to"] == "sindaebang"


def test_supplemented_alternative_is_in_template_when_guard_fails():
    orch, _ = make([
        tools(call("get_elevator_status", station_id="sillim")),
        say("신림역은 고장이라 사당역을 이용하세요."),  # 근거 없는 역
    ])
    res = ask(orch, "거기 말고 다른 역은?")
    assert res.metadata.guard_triggered is True
    assert "사당역" not in res.text and "신대방역" in res.text


def test_no_supplement_when_alternative_already_called_or_station_ok():
    orch, _ = make([
        tools(call("get_elevator_status", station_id="sillim"),
              call("plan_trip", start_id="sillim", end_id="sillim")),
        say("신림역 대신 신대방역을 이용하세요."),
        tools(call("get_elevator_status", station_id="gangnam")),
        say("강남역 엘리베이터는 모두 정상이에요."),
    ])
    res = ask(orch, "신림역 엘리베이터 돼?", session_id="a")
    assert [t.name for t in res.tool_calls] == ["get_elevator_status", "plan_trip"]
    assert "💡 추천 대체역" not in res.text  # 보충 줄을 덧붙이지 않음
    # 대체역 신대방역은 Mock에 엘리베이터 데이터가 없어 미확인 안내가 강제로 붙는다 (R9)
    assert "※ 대체역 신대방역의 엘리베이터 상태는 확인되지 않음" in res.text
    res = ask(orch, "강남역 엘리베이터 돼?", session_id="b")
    assert [t.name for t in res.tool_calls] == ["get_elevator_status"]


def test_no_supplement_for_route_question():
    orch, _ = make([
        tools(call("get_elevator_status", station_id="sadang"),
              call("plan_trip", start_id="sadang", end_id="gangnam", arrive_by="15:00")),
        say("사당역에서 강남역까지 14:34에 출발하세요."),
    ])
    res = ask(orch, "사당에서 강남까지 3시까지 가려면?")
    assert [t.name for t in res.tool_calls] == ["get_elevator_status", "plan_trip"]
    assert res.card_type == "trip_plan"


def test_guard_rejection_with_only_resolve_station_falls_back():
    # 벤치마크 CX08 (2차): 역 확인만 하고 근거 없는 역명을 말한 경우 → 템플릿이 도움말뿐이므로 폴백
    orch, _ = make([
        tools(call("get_elevator_status", station_id="sillim")),
        say("신림역 6번 출구 엘리베이터를 이용할 수 없어요."),
        tools(call("resolve_station", query="신림")),
        say("강남역을 이용하세요."),
    ])
    ask(orch, "신림역 엘리베이터 돼?")
    res = ask(orch, "거기 말고 다른 역은?")
    assert res.metadata.fallback_reason == "guard_rejected"
    assert "신대방역" in res.text and "강남역" not in res.text


# --- 응답 속도 옵션 (이슈 #17) ---

def test_preresolve_puts_station_ids_in_prompt():
    orch, llm = make([
        tools(call("plan_trip", start_id="sindorim", end_id="gangnam", arrive_by="15:00")),
        say("신도림역에서 14:18에 출발하세요."),
    ], preresolve=True)
    res = ask(orch, "신도림에서 강남까지 3시까지 가려면?")
    system_prompt = llm.requests[0][0]["content"]
    assert "station_id=sindorim" in system_prompt and "station_id=gangnam" in system_prompt
    assert [t.name for t in res.tool_calls] == ["resolve_station", "resolve_station", "plan_trip"]
    assert len(llm.requests) == 2  # resolve_station 왕복 없이 2회


def test_preresolve_out_of_coverage_card():
    orch, llm = make([say("죄송해요, 판교역은 지원하지 않는 역이에요.")], preresolve=True)
    res = ask(orch, "판교역 엘리베이터 상태는?")
    assert "지원 범위 밖" in llm.requests[0][0]["content"]
    assert res.card_type == "out_of_coverage"
    assert res.metadata.guard_triggered is False


def test_template_mode_answers_after_data_tool():
    orch, llm = make([tools(call("plan_trip", start_id="sindorim", end_id="gangnam", arrive_by="15:00"))],
                     answer_mode="template")
    res = ask(orch, "신도림에서 강남까지 3시까지 가려면?")
    assert len(llm.requests) == 1
    assert res.metadata.engine == "llm"
    assert "14:18에 출발하세요" in res.text
    assert res.card_type == "trip_plan"


def test_template_mode_keeps_going_after_resolve_only_round():
    orch, llm = make([
        tools(call("resolve_station", query="신림")),
        tools(call("get_elevator_status", station_id="sillim")),
    ], answer_mode="template")
    res = ask(orch, "신림역 엘리베이터 돼?")
    assert len(llm.requests) == 2
    assert "신대방역" in res.text  # 고장 역 대체역 보충 포함


def test_template_mode_states_assumed_origin():
    orch, _ = make([tools(call("plan_trip", start_id="sindorim", end_id="gangnam", arrive_by="17:00"))],
                   answer_mode="template")
    res = ask(orch, "강남역까지 5시까지 가려면 언제 나와야 해?")
    assert res.text.startswith("출발역이 지정되지 않아 신도림역 기준으로 안내합니다.")


def test_template_mode_uses_llm_text_when_no_tools():
    orch, _ = make([say("안녕하세요! 어느 역을 확인해 드릴까요?")], answer_mode="template")
    res = ask(orch, "안녕")
    assert res.text == "안녕하세요! 어느 역을 확인해 드릴까요?"


def test_invalid_answer_mode_is_rejected():
    with pytest.raises(ValueError):
        ChatOrchestrator(client=FakeLLM([]), sessions=SessionStore(), answer_mode="stream")
