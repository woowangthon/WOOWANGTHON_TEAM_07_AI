"""Mock 데이터(mock_data.py)를 DataSource 인터페이스로 감싼 구현."""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from . import mock_data as md

# Mock 데이터의 기준 시각은 '현재 − 5분'으로 고정합니다.
MOCK_FRESHNESS_MIN = 5


class MockDataSource:
    """mock_data.py 상수를 읽는 데이터 소스. 경로는 2호선 순환선 안에서만 계산합니다."""

    # --- 역 ---
    def stations(self) -> Dict[str, dict]:
        return md.STATIONS

    def station(self, station_id: str) -> Optional[dict]:
        return md.STATIONS.get(station_id)

    def aliases(self) -> Dict[str, str]:
        return md.ALIASES

    def names_requiring_suffix(self) -> List[str]:
        return md.NAMES_REQUIRING_SUFFIX

    def out_of_coverage_keywords(self) -> List[str]:
        return md.OUT_OF_COVERAGE_KEYWORDS

    # --- 엘리베이터 ---
    def elevators(self, station_id: str) -> Optional[List[dict]]:
        return md.ELEVATORS.get(station_id)

    def all_elevators(self) -> Dict[str, List[dict]]:
        return md.ELEVATORS

    def data_updated_at(self, now: Optional[datetime] = None) -> Tuple[str, int]:
        base = now or datetime.now()
        stamp = (base - timedelta(minutes=MOCK_FRESHNESS_MIN)).strftime("%Y-%m-%dT%H:%M:00+09:00")
        return stamp, MOCK_FRESHNESS_MIN

    # --- 대체역·경로 ---
    def alternative_candidates(self, station_id: str) -> Optional[dict]:
        """Mock은 대체역을 계산하지 않습니다 (도구가 등록된 안내와 인접 역으로 찾음)."""
        return None

    def registered_alternative(self, station_id: str) -> Optional[dict]:
        return md.ALTERNATIVES.get(station_id)

    def neighbor_stations(self, station_id: str) -> List[str]:
        """2호선 순환선 양옆 역 (외선 방향 먼저). 순환선 밖이면 빈 목록."""
        loop = md.LINE_2_LOOP
        if station_id not in loop:
            return []
        n, idx = len(loop), loop.index(station_id)
        return [loop[(idx + 1) % n], loop[(idx - 1) % n]]

    def route_detail(self, start_id: str, end_id: str) -> Optional[dict]:
        """Mock은 경로 API가 없습니다 (plan_trip이 find_route로 2호선 순환선만 계산)."""
        return None

    def find_route(self, start_id: str, end_id: str) -> Optional[Tuple[List[str], int, str]]:
        """2호선 순환선에서 외선/내선 중 짧은 쪽 (같으면 외선). 순환선 밖 역이면 None."""
        loop = md.LINE_2_LOOP
        if start_id not in loop or end_id not in loop:
            return None
        n = len(loop)
        s, e = loop.index(start_id), loop.index(end_id)
        cw, ccw = (e - s) % n, (s - e) % n
        if cw <= ccw:
            return [loop[(s + i) % n] for i in range(cw + 1)], cw, "2"
        return [loop[(s - i) % n] for i in range(ccw + 1)], ccw, "2"

    def timing(self) -> Dict[str, int]:
        return md.TIMING_PARAMS

    def departure_estimate(self, arrival_at_iso: str, travel_minutes: int, buffer_minutes: int,
                           walk_minutes: int, entry_minutes: int, exit_minutes: int,
                           from_station_id: Optional[str] = None, to_station_id: Optional[str] = None) -> Optional[dict]:
        """Mock은 출발 시각을 계산하지 않습니다 (plan_trip이 직접 역산)."""
        return None
