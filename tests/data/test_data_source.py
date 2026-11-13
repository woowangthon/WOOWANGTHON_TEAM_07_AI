"""데이터 소스 인터페이스 테스트 (src/data/source.py, mock_source.py)."""

import pytest

from AI.src.data import get_data_source, set_data_source, use_data_source
from AI.src.data.mock_source import MockDataSource
from AI.src.fallback import FallbackEngine
from AI.src.tools import get_broken_elevators, get_elevator_status, plan_trip, resolve_station


class BrokenGangnamSource(MockDataSource):
    """강남역 엘리베이터를 전부 고장으로 바꾼 가짜 데이터 소스."""

    def elevators(self, station_id):
        rows = super().elevators(station_id)
        if station_id == "gangnam":
            return [dict(r, status="UNAVAILABLE") for r in rows]
        return rows

    def all_elevators(self):
        return {sid: self.elevators(sid) for sid in super().all_elevators()}


def test_default_source_is_mock():
    assert isinstance(get_data_source(), MockDataSource)


def test_unknown_source_name_is_rejected(monkeypatch):
    monkeypatch.setenv("AI_DATA_SOURCE", "oracle")
    set_data_source(None)
    try:
        with pytest.raises(ValueError, match="알 수 없는 데이터 소스"):
            get_data_source()
    finally:
        monkeypatch.delenv("AI_DATA_SOURCE")
        set_data_source(None)


def test_tools_read_from_swapped_source():
    assert get_elevator_status("gangnam").overall_status == "AVAILABLE"
    with use_data_source(BrokenGangnamSource()):
        assert get_elevator_status("gangnam").overall_status == "UNAVAILABLE"
        assert "gangnam" in [o.station_id for o in get_broken_elevators().outages]
        trip = plan_trip("sindorim", "gangnam", now=None)
        assert any(d.broken_station_id == "gangnam" for d in trip.detours)
    # with 블록이 끝나면 원래 소스로 돌아옵니다.
    assert get_elevator_status("gangnam").overall_status == "AVAILABLE"


def test_fallback_engine_reads_from_swapped_source():
    class TinySource(MockDataSource):
        def stations(self):
            return {"gangnam": super().station("gangnam")}

        def aliases(self):
            return {}

    with use_data_source(TinySource()):
        mentions = FallbackEngine.extract_mentions("강남에서 홍대까지")
        assert [s["id"] for s in mentions["stations"]] == ["gangnam"]
        assert resolve_station("홍대").coverage == "NOT_FOUND"


def test_mock_route_and_neighbors():
    src = MockDataSource()
    path, stops, line = src.find_route("sindorim", "gangnam")
    assert (path[0], path[-1], stops, line) == ("sindorim", "gangnam", 12, "2")
    assert src.find_route("sindorim", "jongno3ga") is None
    assert src.neighbor_stations("gangnam") == ["gyodae", "yeoksam"]
    assert src.neighbor_stations("jongno3ga") == []
