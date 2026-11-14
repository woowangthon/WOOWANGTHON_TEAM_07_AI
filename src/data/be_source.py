"""BE API를 읽는 데이터 소스 (AI 이슈 #4, #5). 계약: BE/docs/api-contract.md

    AI_DATA_SOURCE=be, BE_API_BASE_URL=http://127.0.0.1:8000

BE가 맡는 것: 역 목록·ID, 엘리베이터 설비 상태(가동중/가동 안됨/미확인 판정, TTL), 대체역 후보(BFS),
    도착 시각 기준 출발 시각 역산(POST /api/departure-time, 분 값은 AI가 보냄).
AI에 남는 것: 별칭·미지원 키워드(문장 해석용), 경로·열차 이동 시간(BE 경로 API #37 전까지 Mock 2호선 계산).

변환 규칙:
    - 역 ID: BE 정수 station_id를 문자열로 ("35"). LLM 도구 인자와 FE payload에 그대로 씁니다.
    - 역명: 괄호 부제와 끝의 '역'을 뗀 기본 이름 ("잠실(송파구청)" → "잠실", "서울역" → "서울").
      원래 이름은 display_name에 둡니다.
    - 설비 상태: "가동중" → AVAILABLE, "가동 안됨" → UNAVAILABLE, null → UNKNOWN (unknown_reason 유지).
    - 위치 분류(type)는 BE에 없어 None. 추정하지 않습니다.

연결 실패·타임아웃·5xx·해석 불가 응답은 DataUnavailableError. 이전 값으로 대신 답하지 않습니다.
BE에는 역 전체 목록 API가 없어(검색어 q 필수) 처음 필요할 때 역 상세를 ID 순으로 읽어 캐시합니다.
"""

import logging
import re
from typing import Dict, List, Optional, Tuple

import httpx

from . import mock_data as md
from .mock_source import MockDataSource
from .source import DataUnavailableError

logger = logging.getLogger(__name__)

STATUS_MAP = {"가동중": "AVAILABLE", "가동 안됨": "UNAVAILABLE"}
STATION_STATUS_MAP = {"AVAILABLE": "AVAILABLE", "PARTIAL": "PARTIAL", "UNAVAILABLE": "UNAVAILABLE"}
SYNTHETIC_MARK = "[DEMO/SYNTHETIC]"

# 역 목록을 ID 순으로 읽을 때, 404가 이만큼 연속되면 끝으로 봅니다.
CATALOG_STOP_AFTER_MISSES = 20
CATALOG_MAX_ID = 5000


def base_name(name: str) -> str:
    """BE 역명에서 괄호 부제와 끝의 '역'을 뗍니다."""
    plain = re.sub(r"\(.*?\)", "", name or "").strip()
    return re.sub(r"역$", "", plain)


class BeDataSource:
    """BE API 데이터 소스. http 인자로 httpx.Client(또는 같은 get 인터페이스)를 넣어 테스트할 수 있습니다."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout: float = 2.0,
                 http: Optional[httpx.Client] = None):
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self._mock = MockDataSource()  # 별칭·키워드·경로·시간 상수
        self._catalog: Optional[Dict[str, dict]] = None
        self._by_name: Dict[str, str] = {}

    # ------------------------------------------------------------------
    def _get(self, path: str, **params) -> Optional[dict]:
        """GET 요청. 404면 None, 그 밖의 실패는 DataUnavailableError."""
        try:
            r = self._http.get(path, params=params or None)
        except httpx.HTTPError as e:
            raise DataUnavailableError(f"BE 연결 실패 ({path}): {type(e).__name__}") from e
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise DataUnavailableError(f"BE 응답 오류 ({path}): HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise DataUnavailableError(f"BE 응답 해석 실패 ({path})") from e

    def _post(self, path: str, body: dict) -> dict:
        """POST 요청(JSON 본문). 200이 아니거나 해석할 수 없으면 DataUnavailableError.

        422는 AI가 계약에 맞지 않는 값을 보낸 것(프로그램 오류)이므로 따로 밝힙니다.
        """
        try:
            r = self._http.post(path, json=body)
        except httpx.HTTPError as e:
            raise DataUnavailableError(f"BE 연결 실패 ({path}): {type(e).__name__}") from e
        if r.status_code == 422:
            raise DataUnavailableError(f"BE 요청 형식 오류 ({path}): HTTP 422 — AI가 보낸 값이 계약과 맞지 않음")
        if r.status_code != 200:
            raise DataUnavailableError(f"BE 응답 오류 ({path}): HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise DataUnavailableError(f"BE 응답 해석 실패 ({path})") from e
        if not isinstance(data, dict):
            raise DataUnavailableError(f"BE 응답 해석 실패 ({path}): 객체가 아님")
        return data

    def _load_catalog(self) -> Dict[str, dict]:
        if self._catalog is not None:
            return self._catalog
        catalog: Dict[str, dict] = {}
        misses, sid = 0, 1
        while misses < CATALOG_STOP_AFTER_MISSES and sid <= CATALOG_MAX_ID:
            d = self._get(f"/api/stations/{sid}")
            if d is None:
                misses += 1
            else:
                misses = 0
                key = str(d["station_id"])
                catalog[key] = {
                    "id": key, "name": base_name(d["station_name"]),
                    "display_name": d["station_name"],
                    "lines": [ln["line_id"] for ln in d.get("lines", [])],
                    "limitations": list(d.get("limitations", [])),
                }
            sid += 1
        if not catalog:
            raise DataUnavailableError("BE 역 목록이 비어 있습니다")
        self._catalog = catalog
        self._by_name = {st["name"]: key for key, st in catalog.items()}
        logger.info("BE 역 목록 %d개를 읽었습니다", len(catalog))
        return catalog

    def id_for_name(self, name: str) -> Optional[str]:
        self._load_catalog()
        return self._by_name.get(base_name(name))

    # --- 역 ---
    def stations(self) -> Dict[str, dict]:
        return self._load_catalog()

    def station(self, station_id: str) -> Optional[dict]:
        return self._load_catalog().get(str(station_id))

    def aliases(self) -> Dict[str, str]:
        """AI 별칭 사전(Mock 역 ID 기준)을 역 이름으로 BE ID에 연결합니다."""
        out = {}
        for alias, mock_id in md.ALIASES.items():
            bid = self.id_for_name(md.STATIONS[mock_id]["name"])
            if bid:
                out[alias] = bid
        return out

    def names_requiring_suffix(self) -> List[str]:
        return md.NAMES_REQUIRING_SUFFIX

    def out_of_coverage_keywords(self) -> List[str]:
        return md.OUT_OF_COVERAGE_KEYWORDS

    # --- 엘리베이터 ---
    @staticmethod
    def _segment(ev: dict) -> dict:
        location = ev.get("location_description") or ev.get("elevator_name") or "위치 정보 없음"
        if SYNTHETIC_MARK in (ev.get("elevator_name") or ""):
            location = "시연용 합성 설비(실제 상태 아님)"
        return {
            "segment_id": str(ev["elevator_id"]), "type": None, "location": location,
            "floors": ev.get("operation_section") or "",
            "status": STATUS_MAP.get(ev.get("status"), "UNKNOWN"),
            "unknown_reason": ev.get("unknown_reason") if ev.get("status") not in STATUS_MAP else None,
            "fetched_at": ev.get("fetched_at"), "reason": "",
        }

    def elevators(self, station_id: str) -> Optional[List[dict]]:
        d = self._get(f"/api/stations/{station_id}/elevators")
        if d is None:
            return None
        return [self._segment(ev) for ev in d.get("elevators", [])]

    def all_elevators(self) -> Dict[str, List[dict]]:
        d = self._get("/api/elevators/status") or {}
        out: Dict[str, List[dict]] = {}
        for ev in d.get("elevators", []):
            out.setdefault(str(ev["station_id"]), []).append(self._segment(ev))
        return out

    def data_updated_at(self, now=None) -> Tuple[Optional[str], Optional[int]]:
        """BE는 설비마다 fetched_at을 주므로 별도 기준 시각이 없습니다."""
        return None, None

    # --- 대체역·경로 ---
    def alternative_candidates(self, station_id: str) -> Optional[dict]:
        d = self._get(f"/api/stations/{station_id}/alternatives")
        if d is None:
            return {"candidates": [], "limitations": [], "empty_reason": "역을 찾을 수 없음"}
        return {
            "candidates": [
                {"id": str(a["station_id"]), "name": base_name(a["station_name"]), "stops": a.get("distance"),
                 "same_line": a.get("same_line"),
                 "status": STATION_STATUS_MAP.get((a.get("station_status") or {}).get("status"), "UNKNOWN")}
                for a in d.get("alternatives", [])
            ],
            "limitations": list(d.get("limitations", [])),
            "empty_reason": d.get("empty_reason"),
        }

    def registered_alternative(self, station_id: str) -> Optional[dict]:
        return None  # BE가 대체역을 계산하므로 쓰지 않음

    def neighbor_stations(self, station_id: str) -> List[str]:
        return []  # BE가 대체역을 계산하므로 쓰지 않음

    def _mock_id(self, station_id: str) -> Optional[str]:
        st = self.station(station_id)
        if not st:
            return None
        return next((mid for mid, m in md.STATIONS.items() if m["name"] == st["name"]), None)

    def find_route(self, start_id: str, end_id: str) -> Optional[Tuple[List[str], int, str]]:
        """BE 경로 API(#37)가 없어 Mock 2호선 순환선 계산을 역 이름으로 연결해 씁니다 (추정)."""
        s, e = self._mock_id(start_id), self._mock_id(end_id)
        if not s or not e:
            return None
        found = self._mock.find_route(s, e)
        if not found:
            return None
        path, stops, line = found
        be_path = [self.id_for_name(md.STATIONS[mid]["name"]) for mid in path]
        if any(p is None for p in be_path):
            return None
        return be_path, stops, line

    def timing(self) -> Dict[str, int]:
        return self._mock.timing()

    # --- 출발 시각 ---
    def departure_estimate(self, arrival_at_iso: str, travel_minutes: int, buffer_minutes: int,
                           walk_minutes: int, entry_minutes: int, exit_minutes: int) -> Optional[dict]:
        """BE `POST /api/departure-time` (계약 §8.2)으로 권장 출발 시각을 계산합니다.

        분 값은 모두 명시해서 보냅니다 (BE 기본값, 특히 buffer 0분에 맡기지 않음).
        응답에 필요한 시각 필드가 없으면 해석 실패로 봅니다.
        """
        body = {
            "arrival_at": arrival_at_iso,
            "travel_minutes": travel_minutes,
            "buffer_minutes": buffer_minutes,
            "walk_to_station_minutes": walk_minutes,
            "station_entry_minutes": entry_minutes,
            "exit_walk_minutes": exit_minutes,
        }
        d = self._post("/api/departure-time", body)
        required = ("recommended_departure_at", "estimated_boarding_at", "arrival_at")
        if any(not isinstance(d.get(k), str) for k in required):
            raise DataUnavailableError("BE 응답 해석 실패 (/api/departure-time): 시각 필드 없음")
        return d
