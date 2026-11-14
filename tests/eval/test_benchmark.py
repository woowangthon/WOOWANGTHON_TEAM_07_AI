"""벤치마크 평가셋·채점 테스트 (src/eval/)."""

import re
from collections import Counter

import pytest

from AI.src.data.mock_data import LINES, STATIONS
from AI.src.eval.benchmark import (
    CATEGORIES, format_report, load_cases, make_orchestrator, run_cases, score_case, slots_from_calls, summarize,
)
from AI.src.models import ChatResponse, ToolCallRecord

CASES = load_cases()
CARD_TYPES = {"help", "outages_list", "outages_empty", "out_of_coverage", "need_station",
              "trip_plan", "trip_unsupported", "elevator_status"}
TOOLS = {"resolve_station", "get_elevator_status", "get_broken_elevators", "plan_trip"}


def test_case_set_matches_spec_composition():
    cases = CASES["cases"]
    assert len(cases) == 50
    assert Counter(c["category"] for c in cases) == {
        "elevator": 10, "alternative": 10, "departure": 15, "context": 10, "coverage": 5}
    assert len({c["id"] for c in cases}) == 50


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["id"])
def test_case_expectations_are_valid(case):
    exp = case["expect"]
    assert case["category"] in CATEGORIES
    assert case["turns"] and all(isinstance(t, str) and t for t in case["turns"])
    assert set(exp["card_type"]) <= CARD_TYPES
    for key in ("tools", "optional_tools", "tools_any"):
        assert set(exp.get(key, [])) <= TOOLS
    for slot in exp["slots"]:
        kind, value = slot.split(":", 1)
        if kind in ("station", "origin", "destination"):
            assert value in STATIONS, slot
        elif kind in ("arrive_by", "depart_at"):
            assert re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value), slot
        else:
            assert kind == "line" and value in LINES, slot


def test_slots_from_calls():
    calls = [
        ToolCallRecord(name="resolve_station", args={"query": "사당"}),
        ToolCallRecord(name="get_elevator_status", args={"station_id": "sadang"}),
        ToolCallRecord(name="plan_trip", args={"start_id": "sadang", "end_id": "sadang"}),
        ToolCallRecord(name="plan_trip", args={"start_id": "sindorim", "end_id": "gangnam", "arrive_by": "15:00"}),
        ToolCallRecord(name="get_broken_elevators", args={"line_id": "2"}),
    ]
    assert slots_from_calls(calls) == {
        "station:sadang", "origin:sindorim", "destination:gangnam", "arrive_by:15:00", "line:2"}


def _resp(card, calls, text):
    return ChatResponse(session_id="s", text=text, card_type=card,
                        tool_calls=[ToolCallRecord(name=n, args=a) for n, a in calls])


def test_score_case_pass_and_failures():
    case = {"id": "T", "category": "elevator", "turns": ["강남역 엘리베이터 돼?"],
            "expect": {"card_type": ["elevator_status"], "tools": ["get_elevator_status"],
                       "slots": ["station:gangnam"], "text_includes": ["강남역"], "text_excludes": ["역삼역"]}}
    good = score_case(case, _resp("elevator_status", [("get_elevator_status", {"station_id": "gangnam"})], "강남역 정상"))
    assert good.passed and not good.problems

    bad = score_case(case, _resp("trip_plan", [("plan_trip", {"start_id": "a", "end_id": "b"})], "역삼역으로 가세요"))
    assert not bad.passed
    assert not bad.tools_ok and not bad.card_ok and not bad.text_ok and not bad.slots_exact


def test_fallback_baseline_runs_all_cases():
    results = run_cases(CASES, make_orchestrator("fallback"))
    assert len(results) == 50
    assert all(r.engine == "fallback" for r in results)
    summary = summarize(results)
    assert set(summary["by_category"]) == set(CATEGORIES)
    assert "### 엔진: `fallback`" in format_report(summary, results, "fallback")


def test_run_cases_can_filter():
    results = run_cases(CASES, make_orchestrator("fallback"), only=["coverage", "DP01"])
    assert sorted(r.id for r in results) == ["CV01", "CV02", "CV03", "CV04", "CV05", "DP01"]


def test_guard_detail_is_recorded_for_scored_turn():
    from AI.src.llm.client import LLMReply, ToolCallRequest
    from AI.src.llm.orchestrator import ChatOrchestrator
    from AI.src.llm.session import SessionStore

    class MadeUpTime:
        def __init__(self):
            self.n = 0

        def complete(self, messages, tools):
            self.n += 1
            if self.n % 2:
                return LLMReply(tool_calls=[ToolCallRequest(
                    id="c", name="plan_trip",
                    arguments='{"start_id": "sindorim", "end_id": "gangnam", "arrive_by": "15:00"}')])
            return LLMReply(content="14:30에 출발하세요.")  # 도구 결과는 14:18

    doc = {"cases": [{"id": "G1", "category": "departure", "turns": ["신도림에서 강남까지 3시까지?"] * 2,
                      "expect": {"card_type": ["trip_plan"], "tools": ["plan_trip"], "slots": []}}]}
    [r] = run_cases(doc, ChatOrchestrator(client=MadeUpTime(), sessions=SessionStore()))
    assert r.guard_triggered
    assert len(r.guard_detail) == 1 and "14:30" in r.guard_detail[0]  # 준비 대화의 경고는 세지 않음
