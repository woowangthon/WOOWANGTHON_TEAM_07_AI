"""4대 도구(Tools) 단위 테스트."""

from datetime import datetime

import pytest

from AI.src.tools import get_broken_elevators, get_elevator_status, plan_trip, resolve_station

NOON = datetime(2026, 11, 12, 12, 0)


# --- resolve_station ---------------------------------------------------------

@pytest.mark.parametrize("query, expected_id", [
    ("강남역", "gangnam"),
    ("강남", "gangnam"),
    ("동역사", "ddp"),
    ("고터", "express_bus"),
    ("서울역", "seoul"),
    ("선릉 2호선", "seolleung"),
    ("시청(1호선)", "cityhall"),
    ("구로디지털단지역", "guro_digital"),
    ("동대문역사", "ddp"),        # 앞부분이 유일하게 일치
])
def test_resolve_station_supported(query, expected_id):
    res = resolve_station(query)
    assert res.coverage == "SUPPORTED"
    assert res.station_id == expected_id


def test_resolve_station_exact_name_has_full_confidence():
    assert resolve_station("강남역").confidence == 1.0
    assert resolve_station("동역사").confidence == 0.95


@pytest.mark.parametrize("query", ["판교역", "신분당선 정자", "봉은사"])
def test_resolve_station_out_of_coverage(query):
    res = resolve_station(query)
    assert res.coverage == "OUT_OF_COVERAGE"
    assert res.station_id is None


@pytest.mark.parametrize("query", ["강", "신", "을지로", "부평역", ""])
def test_resolve_station_ambiguous_or_unknown_is_not_found(query):
    # 한 글자이거나 후보가 여러 개면 임의의 역으로 확정하지 않는다.
    assert resolve_station(query).coverage == "NOT_FOUND"


def test_resolve_station_seoul_is_not_seoul_univ():
    assert resolve_station("서울").station_id == "seoul"


# --- get_elevator_status / get_broken_elevators ------------------------------

def test_get_elevator_status_available():
    res = get_elevator_status("gangnam")
    assert res.overall_status == "AVAILABLE"
    assert len(res.segments) == 4


def test_get_elevator_status_partial():
    res = get_elevator_status("sadang")
    assert res.overall_status == "PARTIAL"
    assert [s.segment_id for s in res.segments if s.status != "AVAILABLE"] == ["sadang-e3"]


def test_get_elevator_status_unknown_station_has_no_segments():
    res = get_elevator_status("seocho")
    assert res.overall_status == "UNKNOWN"
    assert res.station_name == "서초"


def test_get_broken_elevators_all_and_by_line():
    all_res = get_broken_elevators()
    assert {o.station_id for o in all_res.outages} == {"sadang", "sillim", "sindorim"}
    assert all_res.count == 3

    line4 = get_broken_elevators("4")
    assert [o.station_id for o in line4.outages] == ["sadang"]


# --- plan_trip ----------------------------------------------------------------

def test_plan_trip_reverse_calculation():
    res = plan_trip("sindorim", "gangnam", arrive_by="15:00", now=NOON)
    assert res.is_possible is True
    # 신도림→강남 2호선 12개 역 = 24분. 총 7 + 3 + 5 + 24 + 3 = 42분
    assert res.route.total_stops == 12
    assert res.breakdown.worst_case_wait == 5
    assert res.arrival_time == "15:00"
    assert res.departure_time == "14:18"
    assert res.board_time == "14:33"
    assert res.minutes_from_now == 138
    assert res.is_late is False


def test_plan_trip_forward_calculation():
    res = plan_trip("sindorim", "gangnam", depart_at="09:00", now=NOON)
    assert res.departure_time == "09:00"
    assert res.arrival_time == "09:42"
    assert res.is_late is False


def test_plan_trip_is_late_when_departure_already_passed():
    res = plan_trip("sindorim", "gangnam", arrive_by="12:20", now=NOON)
    assert res.is_late is True


def test_plan_trip_detour_uses_korean_station_name():
    res = plan_trip("gangnam", "sindorim", now=NOON)
    assert len(res.detours) == 1
    d = res.detours[0]
    assert (d.target_type, d.broken_station_id, d.alternative_station_name) == ("end", "sindorim", "문래")
    assert d.surface_distance_m == 900
    assert "mullae" not in " ".join(res.warnings)


def test_plan_trip_detours_for_start_station_too():
    res = plan_trip("sillim", "sadang", now=NOON)
    assert [(d.target_type, d.alternative_station_name) for d in res.detours] == [("start", "신대방"), ("end", "방배")]


def test_plan_trip_outside_line2_loop_is_not_possible():
    res = plan_trip("seoul", "gangnam", now=NOON)
    assert res.is_possible is False
    assert "지원하지 않습니다" in res.warnings[0]


@pytest.mark.parametrize("kwargs", [
    {"arrive_by": "25:00"},
    {"depart_at": "abc"},
    {"start_id": "nowhere"},
])
def test_plan_trip_rejects_invalid_input(kwargs):
    args = {"start_id": "sindorim", "end_id": "gangnam", **kwargs}
    with pytest.raises(ValueError):
        plan_trip(**args, now=NOON)
