"""LLM 도구 정의(TOOL_SPECS)와 실행기(execute_tool) 테스트 (src/tools/registry.py)."""

from AI.src.tools.registry import TOOL_NAMES, TOOL_SPECS, execute_tool


def test_tool_specs_cover_four_tools():
    assert TOOL_NAMES == ["resolve_station", "get_elevator_status", "get_broken_elevators", "plan_trip"]
    for spec in TOOL_SPECS:
        fn = spec["function"]
        assert spec["type"] == "function"
        assert fn["description"]
        assert fn["parameters"]["type"] == "object"
        assert "title" not in fn["parameters"]


def test_plan_trip_spec_exposes_all_parameters_but_not_now():
    params = next(s for s in TOOL_SPECS if s["function"]["name"] == "plan_trip")["function"]["parameters"]
    assert set(params["properties"]) == {"start_id", "end_id", "arrive_by", "depart_at", "home_walk_min"}
    assert params["required"] == ["start_id", "end_id"]
    assert params["properties"]["arrive_by"]["type"] == "string"   # anyOf[str, null] 단순화 확인


def test_execute_tool_returns_dict():
    res = execute_tool("resolve_station", {"query": "강남역"})
    assert res["station_id"] == "gangnam"


def test_execute_tool_reports_errors_instead_of_raising():
    assert "error" in execute_tool("unknown_tool", {})
    assert "error" in execute_tool("plan_trip", {"start_id": "sindorim"})
    assert "error" in execute_tool("plan_trip", {"start_id": "sindorim", "end_id": "gangnam", "arrive_by": "3시"})
    assert "error" in execute_tool("plan_trip", {"start_id": "nowhere", "end_id": "gangnam"})
