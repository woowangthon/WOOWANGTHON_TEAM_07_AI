"""get_elevator_status, get_broken_elevators 단위 테스트 (src/tools/elevator.py)."""

from AI.src.tools import get_broken_elevators, get_elevator_status


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


def test_get_elevator_status_rejects_unregistered_station():
    import pytest
    with pytest.raises(ValueError, match="알 수 없는 역 ID"):
        get_elevator_status("판교")
