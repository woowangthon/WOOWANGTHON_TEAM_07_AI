"""BE 상태 규칙(BE/docs/api-contract.md §6)에 맞춘 판정·안내 테스트 (AI 이슈 #2, #10).

src/tools/elevator.py의 judge·describe_status, src/tools/trip.py의 대체역·limitations,
데이터 소스 장애 응답은 tests/llm/test_data_unavailable.py.
"""

import pytest
from AI.src.data import use_data_source
from AI.src.data.mock_source import MockDataSource
from AI.src.models import ElevatorSegment
from AI.src.tools import get_broken_elevators, get_elevator_status, plan_trip
from AI.src.tools.elevator import (
    LIMIT_ALL_UNKNOWN, LIMIT_LIST_UNKNOWN, LIMIT_NO_ELEVATOR, LIMIT_SOME_UNKNOWN, describe_status, judge,
)
from AI.src.tools.trip import LIMIT_ESTIMATED, find_alternatives


def seg(status, reason=None, sid="x"):
    return ElevatorSegment(segment_id=sid, location="1번 출구", status=status, unknown_reason=reason)


@pytest.mark.parametrize("statuses, expected", [
    (["AVAILABLE", "AVAILABLE"], ("AVAILABLE", True, 0, [])),
    (["AVAILABLE", "UNAVAILABLE"], ("PARTIAL", True, 0, [])),
    (["UNAVAILABLE", "UNAVAILABLE"], ("UNAVAILABLE", True, 0, [])),
    # 일부 미확인: 확인된 설비로 판정을 유지하고 data_fresh=False + 표준 문구 1
    (["AVAILABLE", "UNKNOWN"], ("AVAILABLE", False, 1, [LIMIT_SOME_UNKNOWN])),
    (["UNAVAILABLE", "UNKNOWN", "UNKNOWN"], ("UNAVAILABLE", False, 2, [LIMIT_SOME_UNKNOWN])),
    (["AVAILABLE", "UNAVAILABLE", "UNKNOWN"], ("PARTIAL", False, 1, [LIMIT_SOME_UNKNOWN])),
    # 전부 미확인 / 엘리베이터 없음 → UNKNOWN (BE null)
    (["UNKNOWN", "UNKNOWN"], ("UNKNOWN", False, 2, [LIMIT_ALL_UNKNOWN])),
    ([], ("UNKNOWN", False, 0, [LIMIT_NO_ELEVATOR])),
])
def test_judge_matches_be_rules(statuses, expected):
    assert judge([seg(s) for s in statuses]) == expected


class StaleSource(MockDataSource):
    """강남: 4대 중 2대 STALE, 시청: 전부 STALE, 사당: 고장 1대 + 미확인 1대."""

    def elevators(self, station_id):
        rows = [dict(r) for r in (super().elevators(station_id) or [])]
        if station_id == "gangnam":
            for r in rows[2:]:
                r.update(status="UNKNOWN", unknown_reason="STALE", fetched_at="2026-11-13T09:00:00+09:00")
        if station_id == "cityhall":
            rows = [dict(r, status="UNKNOWN", unknown_reason="STALE") for r in rows]
        if station_id == "sadang":
            rows[0].update(status="UNKNOWN", unknown_reason="NO_DATA")
        return rows or None

    def all_elevators(self):
        return {sid: self.elevators(sid) for sid in super().all_elevators()}


def test_partial_unknown_station_is_not_called_normal():
    with use_data_source(StaleSource()):
        st = get_elevator_status("gangnam")
    assert (st.overall_status, st.data_fresh, st.total_count, st.unknown_count) == ("AVAILABLE", False, 4, 2)
    assert st.limitations == [LIMIT_SOME_UNKNOWN]
    assert st.updated_at == "2026-11-13T09:00:00+09:00"  # 설비 수집 시각
    text = describe_status(st.model_dump())
    assert "2대는 가동 중" in text and "단정할 수 없습니다" in text
    assert "모두 가동" not in text and "정상" not in text


def test_all_unknown_station_is_unknown_not_available():
    with use_data_source(StaleSource()):
        st = get_elevator_status("cityhall")
    assert st.overall_status == "UNKNOWN" and st.limitations == [LIMIT_ALL_UNKNOWN]
    assert "확인할 수 없습니다" in describe_status(st.model_dump())


def test_station_without_elevator_data_is_unknown():
    st = get_elevator_status("bangbae")  # Mock에 엘리베이터 데이터 없음
    assert st.overall_status == "UNKNOWN" and st.limitations == [LIMIT_NO_ELEVATOR]
    assert st.updated_at is None


def test_broken_list_counts_only_confirmed_and_warns_about_unknown():
    with use_data_source(StaleSource()):
        res = get_broken_elevators()
    by_id = {o.station_id: o for o in res.outages}
    assert "cityhall" not in by_id and "gangnam" not in by_id  # 미확인은 고장으로 세지 않음
    assert by_id["sadang"].broken_count == 1 and by_id["sadang"].unknown_count == 1
    assert res.limitations == [LIMIT_LIST_UNKNOWN]
    assert get_broken_elevators().limitations == []  # Mock 기본 데이터는 전부 확인됨


def test_trip_marks_estimate_and_unknown_station():
    plan = plan_trip("sindorim", "bangbae", arrive_by="15:00")
    assert plan.estimated is True
    assert plan.limitations[0] == LIMIT_ESTIMATED
    assert "방배역 엘리베이터 상태를 확인하지 못함" in plan.limitations
    # 미확인 역(방배)에는 대체역을 붙이지 않는다 (이용 불가가 확인된 신도림만)
    assert [d.broken_station_id for d in plan.detours] == ["sindorim"]


def test_registered_alternative_with_unknown_status_is_disclosed():
    detours, limitations = find_alternatives("sadang")
    assert detours[0].alternative_station_id == "bangbae"
    assert detours[0].alternative_status == "UNKNOWN"
    assert detours[0].surface_distance_m is None  # 확인된 거리가 없으면 기본값으로 채우지 않음
    assert limitations == ["대체역 방배역의 엘리베이터 상태는 확인되지 않음"]


class CalcAltSource(MockDataSource):
    """대체역을 데이터 소스가 계산하는 경우 (BE /alternatives)."""

    def alternative_candidates(self, station_id):
        return {
            "candidates": [
                {"id": "yeoksam", "name": "역삼", "stops": 1, "same_line": True, "status": "AVAILABLE"},
                {"id": "gyodae", "name": "교대", "stops": 1, "same_line": True, "status": "PARTIAL"},
            ],
            "limitations": ["실제 접근 가능한 경로와 환승 가능 여부는 검증되지 않음"],
            "empty_reason": None,
        }


def test_source_calculated_alternatives_are_used_as_is():
    with use_data_source(CalcAltSource()):
        detours, limitations = find_alternatives("gangnam")
        alt_plan = plan_trip("sadang", "sadang")
    assert [d.alternative_station_id for d in detours] == ["yeoksam", "gyodae"]
    assert detours[0].stops_away == 1 and detours[0].bus_tip == ""
    assert limitations == ["실제 접근 가능한 경로와 환승 가능 여부는 검증되지 않음"]
    # 대체역만 묻는 경우(출발=도착)는 후보를 모두 돌려준다
    assert len(alt_plan.detours) == 2
