"""4대 도구(Tools) 단위 테스트.

테스트 대상: resolve_station, get_elevator_status, get_broken_elevators, plan_trip
"""

import pytest
from AI.src.tools import (
    resolve_station, get_elevator_status, get_broken_elevators, plan_trip
)


def test_resolve_station_exact_and_alias():
    # 1. 표준 역명 매칭
    res1 = resolve_station("강남역")
    assert res1.coverage == "SUPPORTED"
    assert res1.station_id == "gangnam"
    assert res1.name == "강남"

    # 2. 별칭 매칭 (동역사 -> ddp)
    res2 = resolve_station("동역사")
    assert res2.coverage == "SUPPORTED"
    assert res2.station_id == "ddp"
    assert res2.name == "동대문역사문화공원"

    # 3. 별칭 매칭 (고터 -> express_bus)
    res3 = resolve_station("고터")
    assert res3.coverage == "SUPPORTED"
    assert res3.station_id == "express_bus"


def test_resolve_station_out_of_coverage():
    # 신분당선 / 판교역 커버리지 밖 판정
    res = resolve_station("판교역")
    assert res.coverage == "OUT_OF_COVERAGE"
    assert res.station_id is None


def test_get_elevator_status_segments():
    # 1. 강남역 (정상 AVAILABLE)
    res_gangnam = get_elevator_status("gangnam")
    assert res_gangnam.station_id == "gangnam"
    assert res_gangnam.overall_status == "AVAILABLE"
    assert len(res_gangnam.segments) >= 3

    # 2. 사당역 (일부 고장 PARTIAL)
    res_sadang = get_elevator_status("sadang")
    assert res_sadang.overall_status == "PARTIAL"
    broken_segments = [s for s in res_sadang.segments if s.status != "AVAILABLE"]
    assert len(broken_segments) > 0


def test_get_broken_elevators():
    res = get_broken_elevators()
    assert res.count > 0
    # 사당역, 신림역, 신도림역 등이 포함되어야 함
    station_ids = [o.station_id for o in res.outages]
    assert "sadang" in station_ids or "sindorim" in station_ids


def test_plan_trip_calculation():
    # 신도림 -> 강남 역산 계산 (15:00 도착)
    res = plan_trip(
        start_id="sindorim",
        end_id="gangnam",
        arrive_by="15:00"
    )
    assert res.is_possible is True
    assert res.arrival_time == "15:00"
    # 출발 시각이 15:00보다 이전이어야 함
    assert res.departure_time < "15:00"
    assert res.route.total_stops > 0
    # 보수적 대기 시간(5분) 적용 확인
    assert res.breakdown.worst_case_wait >= 5


def test_plan_trip_with_broken_destination():
    # 사당역은 고장(PARTIAL) 상태이므로 detours에 대체역(방배) 정보가 포함되어야 함
    res = plan_trip(
        start_id="gangnam",
        end_id="sadang"
    )
    assert len(res.detours) > 0
    assert res.detours[0].broken_station_id == "sadang"
    assert len(res.warnings) > 0

