"""방면 문의의 다중 턴 슬롯 수집과 안전 경계. 실제 운영 데이터가 아닌 Mock 기반 테스트."""

from datetime import datetime, timedelta

from fake_llm import FakeLLM

from AI.src.fallback.engine import FallbackEngine
from AI.src.llm.orchestrator import ChatOrchestrator
from AI.src.llm.session import SessionStore
from AI.src.models import ChatRequest
from AI.src.data import DataUnavailableError
from AI.src.tools.travel_context import directional_response
from AI.src.tools.trip import detour_text


def test_onboard_followup_completes_without_llm_or_departure_advice():
    llm = FakeLLM([])
    orch = ChatOrchestrator(client=llm)
    def ask(text):
        return orch.handle(ChatRequest(session_id="travel", message=text))
    first = ask("열차 안인데 목적지 엘리베이터가 고장났어요")
    assert first.card_type == "need_travel_context"
    assert orch.sessions.get("travel").travel["onboard"] is True
    second = ask("현재 강남역, 2호선, 다음 정차역 역삼역")
    assert "목적지 역" in second.text
    assert "현재 역," not in second.text
    final = ask("목적지 사당역, 고장 역 사당역")
    assert final.card_type == "directional_alternatives"
    assert "방배역" in final.text
    assert "앞인지 뒤인지" in final.text
    assert "뒤쪽 후보라면" in final.text
    assert "출발하세요" not in final.text
    assert all(a.type == "openStation" and "path" not in a.payload for a in final.actions)
    assert final.metadata.data_updated_at is not None
    assert [tc.name for tc in final.tool_calls] == ["get_elevator_status", "plan_trip"]
    assert orch.sessions.get("travel").travel is None
    assert llm.requests == []


def test_fallback_can_collect_multiple_turns_without_claiming_direction():
    ctx = {}
    first = FallbackEngine.process("반대 방향 대체역은?", session_ctx=ctx)
    assert first.card_type == "need_travel_context"
    final = FallbackEngine.process("현재 강남역, 목적지 사당역, 고장 역 사당역, 다음 정차역 역삼역, 2호선, 탑승 전", session_ctx=ctx)
    assert final.card_type == "directional_alternatives"
    assert "탑승 전" in final.text
    assert final.metadata.fallback_used


def test_unknown_station_does_not_reuse_previous_slot():
    ctx = {"travel": {"current": "gangnam"}}
    res = directional_response("현재 가짜역, 목적지 사당역", "s", ctx)
    assert "current" not in ctx["travel"]
    assert "확인하지 못한 역" in res.text
    assert res.actions == []


def test_cancel_and_unrelated_message_and_partial_status():
    ctx = {"travel": {"onboard": True}}
    assert directional_response("그만", "s", ctx).card_type == "help"
    assert ctx == {}
    assert directional_response("강남역 상태는?", "s", ctx) is None
    assert "일부 엘리베이터 이용 불가" in detour_text({"alternative_station_name": "후보", "alternative_status": "PARTIAL"})
    assert "가동 확인" not in detour_text({"alternative_station_name": "후보", "alternative_status": "PARTIAL"})


def test_pending_travel_uses_session_ttl():
    now = [datetime(2026, 11, 15, 12)]
    store = SessionStore(ttl_minutes=1, clock=lambda: now[0])
    ctx = store.get("s")
    ctx.travel = {"onboard": True}
    store.save(ctx)
    now[0] += timedelta(minutes=2)
    assert store.get("s").travel is None


def test_normal_direction_qualified_status_and_new_topics_are_not_hijacked():
    assert directional_response("역삼 방면 엘리베이터 상태는?", "s", {}) is None
    for text in ("강남역 엘리베이터 상태는?", "지금 고장난 역 알려줘", "새 질문 강남역은?"):
        ctx = {"travel": {"onboard": True}}
        assert directional_response(text, "s", ctx) is None
        assert "travel" not in ctx


def test_station_suffix_optional_and_broken_station_separate_from_destination():
    ctx = {"travel": {}}
    result = directional_response("현재 강남, 2호선, 목적지 잠실, 고장 역 사당, 다음 정차역 역삼, 승차 중", "s", ctx)
    assert result.card_type == "directional_alternatives"
    assert "목적지 잠실역, 고장 역 사당역" in result.text
    assert result.tool_calls[-1].args == {"start_id": "sadang", "end_id": "sadang"}


def test_conflicting_next_station_keeps_context_for_correction():
    ctx = {"travel": {}}
    result = directional_response("현재 강남, 2호선, 목적지 잠실, 고장 역 사당, 다음 정차역 강남, 승차 중", "s", ctx)
    assert result.card_type == "need_travel_context"
    assert ctx["travel"]["current"] == "gangnam"
    assert "next" not in ctx["travel"]
    assert directional_response("다음 정차역 역삼", "s", ctx).card_type == "directional_alternatives"


def test_invalid_line_does_not_guess_and_empty_pending_slot_survives():
    ctx = {}
    directional_response("반대 방향 대체역은?", "s", ctx)
    assert ctx == {"travel": {}}
    result = directional_response("현재 역삼, 12호선", "s", ctx)
    assert result.card_type == "need_travel_context"
    assert ctx["travel"]["current"] == "yeoksam"
    assert "line" not in ctx["travel"]


def test_server_error_never_becomes_empty_candidates(monkeypatch):
    from AI.src.tools import travel_context
    def fail(*args, **kwargs):
        raise DataUnavailableError("BE unavailable")
    monkeypatch.setattr(travel_context, "plan_trip", fail)
    orch = ChatOrchestrator(client=FakeLLM([]))
    result = orch.handle(ChatRequest(message="현재 강남, 2호선, 목적지 잠실, 고장 역 사당, 다음 정차역 역삼, 승차 중"))
    assert result.card_type == "data_unavailable"
    assert result.metadata.fallback_reason == "data_unavailable"
    assert result.actions == []
