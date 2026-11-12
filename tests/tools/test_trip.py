"""plan_trip 단위 테스트 (src/tools/trip.py)."""

from datetime import datetime

import pytest

from AI.src.tools import plan_trip

NOON = datetime(2026, 11, 12, 12, 0)


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
