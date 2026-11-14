"""데이터 소스 인터페이스.

도구(src/tools/)와 폴백 엔진은 Mock 데이터 모듈을 직접 읽지 않고 이 인터페이스를 거칩니다.
구현: MockDataSource(mock_source.py), BeDataSource(be_source.py, BE API 호출. 이슈 #4, #5).

    from AI.src.data.source import get_data_source
    src = get_data_source()
    src.station("gangnam")

구현 선택: 환경 변수 AI_DATA_SOURCE (기본 "mock", BE 연동은 "be").
테스트에서는 use_data_source(가짜_구현)으로 잠깐 바꿔 끼울 수 있습니다.

계산 위치:
    - 역·엘리베이터·대체역 후보·경로처럼 "데이터에서 찾는 것"은 데이터 소스가 맡습니다.
      (BE 연동 후에는 BE API가 계산한 결과를 돌려줍니다.)
    - 상태 판정(PARTIAL 등), 안내 문장 조립은 도구가 맡습니다.
    - 출발 시각 역산은 데이터 소스가 계산하면(BE `departure_estimate`) 그 결과를, 아니면(Mock) 도구가 계산합니다.
"""

import os
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, Iterator, List, Optional, Protocol, Tuple


class DataUnavailableError(RuntimeError):
    """데이터 소스(BE)에 연결할 수 없거나 응답을 해석할 수 없을 때.

    도구는 이 오류를 그대로 올리고, 응답은 "확인할 수 없음"으로 안내합니다 (이전 값이나 Mock 값으로 답하지 않음).
    """


class DataSource(Protocol):
    """도구와 폴백 엔진이 쓰는 데이터 조회 인터페이스.

    역 dict 형식: {"id": str, "name": str ('역'·괄호 부제 없음), "lines": ["2", ...]}
    엘리베이터 dict 형식: ElevatorSegment 필드와 같음
        {"segment_id", "type", "location", "floors", "status", "unknown_reason", "fetched_at", "reason"}
    BE 구현의 메서드는 연결 실패 시 DataUnavailableError를 냅니다.
    """

    # --- 역 ---
    def stations(self) -> Dict[str, dict]:
        """지원 역 전체 (키: 역 ID)."""

    def station(self, station_id: str) -> Optional[dict]:
        """역 하나. 없으면 None."""

    def aliases(self) -> Dict[str, str]:
        """별칭('역'을 뗀 형태) → 역 ID."""

    def names_requiring_suffix(self) -> List[str]:
        """문장에서 뒤에 '역'이 붙어야 역으로 인정하는 이름 (예: 서울, 이대)."""

    def out_of_coverage_keywords(self) -> List[str]:
        """서비스 범위 밖 역·노선·지역 키워드."""

    # --- 엘리베이터 ---
    def elevators(self, station_id: str) -> Optional[List[dict]]:
        """역의 엘리베이터 구간 목록. 데이터가 없으면 None."""

    def all_elevators(self) -> Dict[str, List[dict]]:
        """엘리베이터 데이터가 있는 모든 역 (키: 역 ID). 전체 운행 상태 조회용."""

    def data_updated_at(self, now: Optional[datetime] = None) -> Tuple[Optional[str], Optional[int]]:
        """설비에 수집 시각이 없을 때 쓰는 데이터 기준 시각(ISO-8601 KST)과 경과 분. 모르면 (None, None)."""

    # --- 대체역·경로 ---
    def alternative_candidates(self, station_id: str) -> Optional[dict]:
        """데이터 소스가 직접 계산한 대체역 후보. 계산 기능이 없으면 None (도구가 아래 두 메서드로 찾음).

        형식: {"candidates": [{"id", "name", "stops", "same_line", "status"}, ...] (추천 순),
               "limitations": [str, ...], "empty_reason": Optional[str]}
        후보는 엘리베이터 가동이 확인된 역(AVAILABLE·PARTIAL)만 담습니다.
        """

    def registered_alternative(self, station_id: str) -> Optional[dict]:
        """고장 역에 등록된 대체역 안내 {"alt_id", "distance_m", "walk_min", "tip"}. 없으면 None."""

    def neighbor_stations(self, station_id: str) -> List[str]:
        """인접 역 ID 목록 (가까운 순). 대체역 후보 탐색용."""

    def route_detail(self, start_id: str, end_id: str) -> Optional[dict]:
        """데이터 소스가 직접 계산한 경로 (BE `GET /api/routes`, 계약 §8.4). 계산 기능이 없으면 None (도구가 find_route를 씀).

        형식: {"found": False}  (BE 404: 두 역을 잇는 경로 없음)
              {"found": True, "path": [역 ID], "legs": [{"line_id", "from_id", "to_id", "stops", "ride_minutes"}],
               "transfer_station_ids": [역 ID], "transfers": int, "total_minutes": int,
               "ride_minutes": int, "transfer_minutes": int,
               "elevator_warning": {"status", "items": [{"station_id", "station_name", "kind", "message"}], "message"},
               "limitations": [str, ...]}
        """

    def find_route(self, start_id: str, end_id: str) -> Optional[Tuple[List[str], int, str]]:
        """(경로 역 ID 목록, 정거장 수, 노선 ID). 계산할 수 없는 구간이면 None. route_detail이 None일 때만 씁니다."""

    def timing(self) -> Dict[str, int]:
        """시간 계산 상수 (분). 키는 mock_data.TIMING_PARAMS와 같음."""

    def departure_estimate(
        self, arrival_at_iso: str, travel_minutes: int, buffer_minutes: int,
        walk_minutes: int, entry_minutes: int, exit_minutes: int,
        from_station_id: Optional[str] = None, to_station_id: Optional[str] = None,
    ) -> Optional[dict]:
        """도착 희망 시각에서 거꾸로 계산한 권장 출발 시각. 계산 기능이 없으면 None (도구가 직접 계산).

        arrival_at_iso: 오프셋이 있는 ISO 8601 초 단위 (예: "2026-12-01T15:00:00+09:00").
        from_station_id, to_station_id: 함께 주면 BE가 경로 위 역의 엘리베이터 경고를 계산하고,
            환승 없는 경로면 시간표로 열차를 고를 수 있습니다 (timetable_used, selected_train).
        반환 형식: BE `POST /api/departure-time` 응답 (BE/docs/api-contract.md §8.2)
            {"recommended_departure_at", "estimated_boarding_at", "estimated_alighting_at", "arrival_at",
             "total_minutes", "limitations": [str, ...], ...}  시각은 KST(+09:00) ISO 문자열.
        모든 분 값은 호출하는 쪽(도구)이 정해서 보냅니다. 데이터 소스의 기본값에 맡기지 않습니다.
        """


def _create(name: str) -> DataSource:
    if name == "mock":
        from .mock_source import MockDataSource
        return MockDataSource()
    if name == "be":
        from ..config import settings
        from .be_source import BeDataSource
        return BeDataSource(settings.BE_API_BASE_URL, settings.BE_API_TIMEOUT_SECONDS)
    raise ValueError(f"알 수 없는 데이터 소스입니다: {name!r} (현재 지원: mock, be)")


_current: Optional[DataSource] = None


def get_data_source() -> DataSource:
    """현재 데이터 소스. 처음 호출할 때 AI_DATA_SOURCE(기본 mock)로 만듭니다."""
    global _current
    if _current is None:
        _current = _create(os.getenv("AI_DATA_SOURCE", "mock").strip().lower() or "mock")
    return _current


def set_data_source(source: Optional[DataSource]) -> None:
    """데이터 소스를 바꿉니다. None이면 다음 호출 때 환경 변수 기준으로 다시 만듭니다."""
    global _current
    _current = source


@contextmanager
def use_data_source(source: DataSource) -> Iterator[DataSource]:
    """with 블록 안에서만 데이터 소스를 바꿉니다 (테스트용)."""
    global _current
    previous = _current
    _current = source
    try:
        yield source
    finally:
        _current = previous


def station_name(station_id: Optional[str]) -> str:
    """역 ID의 표준 역명. 모르는 ID면 ID를 그대로 반환합니다."""
    if not station_id:
        return ""
    st = get_data_source().station(station_id)
    return st["name"] if st else station_id
