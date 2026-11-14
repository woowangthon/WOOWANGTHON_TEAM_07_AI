"""데이터 소스(BE) 장애와 limitations 강제 (AI 이슈 #10, BE 계약 R9)."""

import pytest
from fake_llm import FakeLLM, call, say, tools, unavailable

from AI.src.data import DataUnavailableError, use_data_source
from AI.src.data.mock_source import MockDataSource
from AI.src.fallback import FallbackEngine
from AI.src.llm.orchestrator import ChatOrchestrator
from AI.src.llm.session import SessionStore
from AI.src.models import ChatRequest
from AI.src.tools.elevator import LIMIT_SOME_UNKNOWN


class StaleGangnamSource(MockDataSource):
    """강남역 4대 중 2대가 STALE."""

    def elevators(self, station_id):
        rows = [dict(r) for r in (super().elevators(station_id) or [])]
        if station_id == "gangnam":
            for r in rows[2:]:
                r.update(status="UNKNOWN", unknown_reason="STALE")
        return rows or None


class DownSource(MockDataSource):
    def elevators(self, station_id):
        raise DataUnavailableError("BE 연결 실패")

    def all_elevators(self):
        raise DataUnavailableError("BE 연결 실패")


def _orch(script):
    return ChatOrchestrator(client=FakeLLM(script), sessions=SessionStore())


def test_llm_path_answers_unavailable_when_data_source_down():
    orch = _orch([tools(call("get_elevator_status", station_id="gangnam")),
                  say("강남역 엘리베이터는 정상입니다.")])  # LLM이 지어낸 답은 쓰지 않는다
    with use_data_source(DownSource()):
        res = orch.handle(ChatRequest(session_id="s", message="강남역 엘리베이터 돼?", current_time="12:00"))
    assert res.card_type == "data_unavailable"
    assert "확인할 수 없습니다" in res.text and "정상" not in res.text


def test_fallback_path_answers_unavailable_when_data_source_down():
    orch = ChatOrchestrator(client=FakeLLM([unavailable("timeout")]), sessions=SessionStore())  # LLM 실패 → 폴백
    with use_data_source(DownSource()):
        res = orch.handle(ChatRequest(session_id="s", message="지금 고장난 역 알려줘", current_time="12:00"))
    assert res.card_type == "data_unavailable"
    assert res.metadata.fallback_reason == "data_unavailable"


def test_fallback_engine_raises_so_orchestrator_can_decide():
    with use_data_source(DownSource()), pytest.raises(DataUnavailableError):
        FallbackEngine.process("강남역 엘리베이터 돼?")


def test_limitations_are_forced_into_llm_text():
    orch = _orch([tools(call("get_elevator_status", station_id="gangnam")),
                  say("강남역 엘리베이터는 확인된 2대가 가동 중이에요.")])
    with use_data_source(StaleGangnamSource()):
        res = orch.handle(ChatRequest(session_id="s", message="강남역 엘리베이터 돼?", current_time="12:00"))
    assert res.metadata.guard_triggered is False
    assert res.text.startswith("강남역 엘리베이터는 확인된 2대")
    assert f"※ {LIMIT_SOME_UNKNOWN}" in res.text


def test_unavailable_metadata_is_same_on_llm_and_fallback_paths():
    llm_path = _orch([tools(call("get_elevator_status", station_id="gangnam")), say("정상입니다.")])
    fb_path = ChatOrchestrator(client=FakeLLM([unavailable("timeout")]), sessions=SessionStore())
    with use_data_source(DownSource()):
        a = llm_path.handle(ChatRequest(session_id="s", message="강남역 엘리베이터 돼?", current_time="12:00"))
        b = fb_path.handle(ChatRequest(session_id="s", message="강남역 엘리베이터 돼?", current_time="12:00"))
    for res in (a, b):
        assert (res.card_type, res.metadata.engine, res.metadata.fallback_reason) == \
            ("data_unavailable", "fallback", "data_unavailable")
