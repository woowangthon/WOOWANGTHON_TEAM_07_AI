"""Mock 데이터 무결성 테스트. 데이터를 고칠 때 깨진 참조를 바로 잡아냅니다."""

from AI.src.data.mock_data import (
    ALIASES, ALTERNATIVES, ELEVATORS, LINE_2_LOOP, LINES, STATIONS,
)


def test_every_referenced_station_is_registered():
    referenced = set(LINE_2_LOOP) | set(ALIASES.values()) | set(ELEVATORS) | set(ALTERNATIVES)
    referenced |= {v["alt_id"] for v in ALTERNATIVES.values()}
    assert referenced - set(STATIONS) == set()


def test_station_records_are_consistent():
    for sid, st in STATIONS.items():
        assert st["id"] == sid
        assert not st["name"].endswith("역"), f"{sid}: 역명에는 '역'을 붙이지 않는다"
        assert st["lines"] and set(st["lines"]) <= set(LINES)


def test_line2_loop_has_no_duplicates():
    assert len(LINE_2_LOOP) == len(set(LINE_2_LOOP)) == 43


def test_aliases_do_not_shadow_station_names():
    names = {st["name"] for st in STATIONS.values()}
    assert set(ALIASES) & names == set()
