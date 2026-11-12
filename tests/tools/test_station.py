"""resolve_station 단위 테스트 (src/tools/station.py)."""

import pytest

from AI.src.tools import resolve_station


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
