"""metadata.data_updated_at: 근거 데이터의 수집 시각만 싣고, 모르면 None (AI 이슈 #10).

응답 생성 시각("지금")으로 채우면 오래된 데이터가 방금 갱신된 것처럼 보이므로 금지한다.
"""

from datetime import datetime

import pytest
from fake_llm import FakeLLM, call, say, tools, unavailable

from AI.src.data import DataUnavailableError, use_data_source
from AI.src.data.mock_source import MockDataSource
from AI.src.fallback import FallbackEngine
from AI.src.llm.orchestrator import ChatOrchestrator
from AI.src.llm.session import SessionStore
from AI.src.models import ChatRequest, ChatResponse, ChatResponseMetadata

# BE 데모 데이터처럼 하루 전에 수집된(오래된) 시각
STAMP = "2026-11-13T09:00:00+09:00"


class StampedSource(MockDataSource):
    """모든 설비에 같은 수집 시각(fetched_at)을 붙인 Mock."""

    @staticmethod
    def _stamp(rows):
        return [dict(r, fetched_at=STAMP) for r in rows]

    def elevators(self, station_id):
        rows = super().elevators(station_id)
        return self._stamp(rows) if rows is not None else None

    def all_elevators(self):
        return {sid: self._stamp(rows) for sid, rows in super().all_elevators().items()}


class DownSource(MockDataSource):
    def elevators(self, station_id):
        raise DataUnavailableError("BE 연결 실패")

    def all_elevators(self):
        raise DataUnavailableError("BE 연결 실패")


def _orch(script, answer_mode="llm"):
    return ChatOrchestrator(client=FakeLLM(script), sessions=SessionStore(),
                            preresolve=False, answer_mode=answer_mode)


def _ask(orch, message):
    return orch.handle(ChatRequest(session_id="s", message=message, current_time="12:00"))


def _fallback(message):
    return FallbackEngine.process(message, "s", "12:00")


def _assert_not_now(value):
    """None이 아니면 응답 시각이 아니라 도구가 준 수집 시각이어야 한다."""
    assert value is None or value == STAMP
    now_prefix = datetime.now().strftime("%Y-%m-%dT%H:%M")
    assert not (value or "").startswith(now_prefix)


# ---------------------------------------------------------------------------
# 기본값
# ---------------------------------------------------------------------------
def test_metadata_default_is_none_not_now():
    assert ChatResponseMetadata().data_updated_at is None
    assert ChatResponse(session_id="s", text="t").metadata.data_updated_at is None
    assert ChatResponse(session_id="s", text="t").model_dump()["metadata"]["data_updated_at"] is None


# ---------------------------------------------------------------------------
# LLM 경로
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["llm", "template"])
def test_llm_elevator_status_carries_tool_updated_at(mode):
    script = [tools(call("get_elevator_status", station_id="gangnam"))]
    if mode == "llm":
        script.append(say("강남역 엘리베이터 상태를 확인했어요."))
    with use_data_source(StampedSource()):
        res = _ask(_orch(script, mode), "강남역 엘리베이터 돼?")
    assert res.card_type == "elevator_status"
    assert res.metadata.data_updated_at == STAMP


def test_llm_partial_station_with_alternative_keeps_status_updated_at():
    script = [tools(call("get_elevator_status", station_id="sadang"),
                    call("plan_trip", start_id="sadang", end_id="sadang"))]
    with use_data_source(StampedSource()):
        res = _ask(_orch(script, "template"), "사당역 엘리베이터 돼?")
    assert res.card_type == "elevator_status"
    assert res.metadata.data_updated_at == STAMP


def test_llm_station_without_elevator_data_is_none():
    script = [tools(call("get_elevator_status", station_id="euljiro3ga"))]
    with use_data_source(StampedSource()):
        res = _ask(_orch(script, "template"), "을지로3가역 엘리베이터 돼?")
    assert res.card_type == "elevator_status"
    assert "확인할 수 없습니다" in res.text
    assert res.metadata.data_updated_at is None


def test_llm_outages_carries_tool_updated_at():
    script = [tools(call("get_broken_elevators"))]
    with use_data_source(StampedSource()):
        res = _ask(_orch(script, "template"), "지금 고장난 역 알려줘")
    assert res.card_type in ("outages_list", "outages_empty")
    assert res.metadata.data_updated_at == STAMP


def test_llm_help_is_none():
    res = _ask(_orch([say("안녕하세요! 어떤 역을 도와드릴까요?")]), "안녕")
    assert res.card_type == "help"
    assert res.metadata.data_updated_at is None


def test_llm_out_of_coverage_is_none():
    script = [tools(call("resolve_station", query="판교")), say("죄송해요. 그 지역은 아직 지원하지 않아요.")]
    res = _ask(_orch(script), "판교역 엘리베이터 돼?")
    assert res.card_type == "out_of_coverage"
    assert res.metadata.data_updated_at is None


def test_llm_trip_plan_is_none():
    """plan_trip 결과에는 수집 시각 필드가 없으므로 지어내지 않는다."""
    script = [tools(call("plan_trip", start_id="sindorim", end_id="gangnam", arrive_by="15:00"))]
    with use_data_source(StampedSource()):
        res = _ask(_orch(script, "template"), "신도림에서 강남까지 3시까지 가려면?")
    assert res.card_type == "trip_plan"
    assert res.metadata.data_updated_at is None


def test_llm_data_unavailable_is_none():
    script = [tools(call("get_elevator_status", station_id="gangnam")), say("강남역 엘리베이터는 정상입니다.")]
    with use_data_source(DownSource()):
        res = _ask(_orch(script), "강남역 엘리베이터 돼?")
    assert res.card_type == "data_unavailable"
    assert res.metadata.data_updated_at is None


# ---------------------------------------------------------------------------
# 폴백 경로
# ---------------------------------------------------------------------------
def test_fallback_elevator_status_carries_tool_updated_at():
    with use_data_source(StampedSource()):
        res = _fallback("강남역 엘리베이터 돼?")
    assert res.card_type == "elevator_status"
    assert res.metadata.data_updated_at == STAMP


def test_fallback_outages_carries_tool_updated_at():
    with use_data_source(StampedSource()):
        res = _fallback("지금 고장난 역 알려줘")
    assert res.card_type in ("outages_list", "outages_empty")
    assert res.metadata.data_updated_at == STAMP


def test_fallback_station_without_elevator_data_is_none():
    with use_data_source(StampedSource()):
        res = _fallback("을지로3가역 엘리베이터 돼?")
    assert res.card_type == "elevator_status"
    assert res.metadata.data_updated_at is None


@pytest.mark.parametrize("message, card_type", [
    ("안녕", "help"),
    ("판교역 엘리베이터 돼?", "out_of_coverage"),
    ("부평역 엘리베이터 돼?", "need_station"),
    ("신도림에서 강남까지 3시까지 가려면?", "trip_plan"),
])
def test_fallback_responses_without_data_time_are_none(message, card_type):
    with use_data_source(StampedSource()):
        res = _fallback(message)
    assert res.card_type == card_type
    assert res.metadata.data_updated_at is None


def test_orchestrator_fallback_keeps_tool_updated_at():
    """LLM 실패로 폴백 전환해도 폴백이 쓴 도구 결과의 수집 시각을 그대로 싣는다."""
    with use_data_source(StampedSource()):
        res = _ask(_orch([unavailable("timeout")]), "강남역 엘리베이터 돼?")
    assert res.metadata.engine == "fallback" and res.metadata.fallback_reason == "timeout"
    assert res.metadata.data_updated_at == STAMP


def test_fallback_data_unavailable_is_none():
    with use_data_source(DownSource()):
        res = _ask(_orch([unavailable("timeout")]), "지금 고장난 역 알려줘")
    assert res.card_type == "data_unavailable"
    assert res.metadata.data_updated_at is None


# ---------------------------------------------------------------------------
# 어떤 응답도 응답 시각으로 채우지 않는다
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("message", [
    "강남역 엘리베이터 돼?", "사당역 엘리베이터 돼?", "을지로3가역 엘리베이터 돼?", "지금 고장난 역 알려줘",
    "안녕", "판교역 엘리베이터 돼?", "신도림에서 강남까지 3시까지 가려면?", "사당역 대체역은?",
])
def test_fallback_never_uses_response_time(message):
    with use_data_source(StampedSource()):
        res = _fallback(message)
    _assert_not_now(res.metadata.data_updated_at)
