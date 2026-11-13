"""후처리 환각 검증기 테스트 (src/llm/guard.py)."""

from datetime import datetime

from AI.src.llm.guard import check_text, extract_times
from AI.src.llm.response_builder import ToolResult
from AI.src.tools.registry import execute_tool


def run(name, **args):
    return ToolResult(name, args, execute_tool(name, args, now=datetime(2026, 11, 13, 12, 0)))


def test_extract_times():
    assert extract_times("14:18 출발, 2시 30분 탑승, 3시 반 도착, 3시간 걸림") == [(14, 18), (2, 30), (3, 30)]


def test_text_matching_tool_results_passes():
    results = [run("plan_trip", start_id="sindorim", end_id="gangnam", arrive_by="15:00")]
    text = "신도림역에서 14:18에 출발하시면 오후 2시 33분 열차를 타고 3시까지 강남역에 도착해요."
    assert check_text(text, results, "신도림에서 강남까지 3시까지 가려면?", "12:00").ok


def test_made_up_time_is_caught():
    results = [run("plan_trip", start_id="sindorim", end_id="gangnam", arrive_by="15:00")]
    verdict = check_text("14시 30분에 출발하세요.", results, "", "12:00")
    assert not verdict.ok
    assert verdict.unknown_times == ["14:30"]


def test_made_up_station_is_caught():
    results = [run("get_elevator_status", station_id="gangnam")]
    verdict = check_text("강남역은 정상이에요. 신논현역도 괜찮아요.", results, "강남역 엘리베이터 돼?")
    assert verdict.unknown_stations == ["신논현역"]


def test_alternative_station_from_tool_is_allowed():
    results = [run("plan_trip", start_id="sadang", end_id="sadang")]
    assert check_text("사당역 대신 인접한 방배역을 이용하세요.", results, "사당역 고장났대").ok


def test_generic_words_and_user_mentions_are_allowed():
    results = [run("resolve_station", query="부평역")]
    text = "죄송해요, 부평역은 아직 지원하지 않아요. 해당지역 대체역 정보도 없어요. 다른 역을 알려 주세요."
    assert check_text(text, results, "부평역 엘리베이터 돼?").ok
