"""BE API를 읽는 데이터 소스 (AI 이슈 #4, #5). 계약: BE/docs/api-contract.md

    AI_DATA_SOURCE=be, BE_API_BASE_URL=http://127.0.0.1:8000

BE가 맡는 것: 역 목록·ID, 엘리베이터 설비 상태(가동중/가동 안됨/미확인 판정, TTL), 대체역 후보(BFS),
    경로(GET /api/routes, 환승 포함, 추정 소요 시간, 경로 위 엘리베이터 경고),
    도착 시각 기준 출발 시각 역산(POST /api/departure-time, 분 값과 역 ID는 AI가 보냄. 결정 D17).
AI에 남는 것: 별칭·미지원 키워드(문장 해석용), 도보·진입·대기·출구 시간 상수(BE 기본값과 같음).

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
        self._mock = MockDataSource()  # 시간 상수 (BE 기본값과 같음)
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

    def route_detail(self, start_id: str, end_id: str) -> dict:
        """BE `GET /api/routes?from=&to=` (계약 §8.4)을 AI 형식으로 바꿉니다.

        404는 경로 없음({"found": False}). 역 존재는 plan_trip이 먼저 확인하므로 404는 "두 역을 잇는 경로 없음"입니다.
        필요한 필드가 없거나 모양이 다르면 DataUnavailableError. 값을 채우거나 추정하지 않습니다.
        """
        d = self._get("/api/routes", **{"from": int(start_id), "to": int(end_id)})
        if d is None:
            return {"found": False}
        try:
            return _convert_route(d)
        except (KeyError, TypeError, ValueError) as e:
            raise DataUnavailableError(f"BE 응답 해석 실패 (/api/routes): {type(e).__name__} {e}") from e

    def find_route(self, start_id: str, end_id: str) -> Optional[Tuple[List[str], int, str]]:
        """route_detail의 요약 (역 ID 목록, 정거장 수, 첫 노선). 경로가 없으면 None.

        plan_trip은 출발≠도착이면 route_detail을 쓰고, 이 메서드는 출발=도착(대체역만 묻기)일 때만 부릅니다.
        BE는 같은 역끼리의 경로를 422로 거절하므로 호출하지 않습니다.
        """
        if str(start_id) == str(end_id):
            return [str(start_id)], 0, ""
        r = self.route_detail(start_id, end_id)
        if not r["found"]:
            return None
        return r["path"], sum(leg["stops"] for leg in r["legs"]), r["legs"][0]["line_id"] if r["legs"] else ""

    def timing(self) -> Dict[str, int]:
        return self._mock.timing()

    # --- 출발 시각 ---
    def departure_estimate(self, arrival_at_iso: str, travel_minutes: int, buffer_minutes: int,
                           walk_minutes: int, entry_minutes: int, exit_minutes: int,
                           from_station_id: Optional[str] = None, to_station_id: Optional[str] = None) -> Optional[dict]:
        """BE `POST /api/departure-time` (계약 §8.2)으로 권장 출발 시각을 계산합니다.

        분 값은 모두 명시해서 보냅니다. AI 값(도보 7·진입 3·대기 5·출구 3)은 BE 기본값과 같지만,
        사용자가 도보 시간을 바꿀 수 있고 BE 기본값이 바뀌어도 AI 안내와 어긋나지 않게 하려는 것입니다.
        역 ID를 함께 주면 BE가 경로 위 엘리베이터 경고를 계산하고 시간표로 열차를 고를 수 있습니다.
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
        if from_station_id is not None and to_station_id is not None:
            body["from_station_id"] = int(from_station_id)
            body["to_station_id"] = int(to_station_id)
        d = self._post("/api/departure-time", body)
        required = ("recommended_departure_at", "estimated_boarding_at", "arrival_at")
        if any(not isinstance(d.get(k), str) for k in required):
            raise DataUnavailableError("BE 응답 해석 실패 (/api/departure-time): 시각 필드 없음")
        train = d.get("selected_train")
        if train is not None and not (isinstance(train, dict) and all(
                isinstance(train.get(k), str) for k in ("train_no", "boarding_at", "alighting_at"))):
            raise DataUnavailableError("BE 응답 해석 실패 (/api/departure-time): selected_train 형식")
        if d.get("elevator_warning") is not None:
            try:
                d["elevator_warning"] = _convert_warning(d["elevator_warning"])
            except (KeyError, TypeError, ValueError) as e:
                raise DataUnavailableError("BE 응답 해석 실패 (/api/departure-time): elevator_warning 형식") from e
        return d


WARNING_STATUSES = ("WARNING", "UNCONFIRMED", "CLEAR")
WARNING_KINDS = ("UNAVAILABLE", "PARTIAL", "UNCONFIRMED")


def _convert_warning(w: dict) -> dict:
    """BE elevator_warning (계약 §8.4) → 역 ID 문자열, 기본 역명. status·kind가 계약 밖 값이면 ValueError."""
    if w["status"] not in WARNING_STATUSES:
        raise ValueError(f"elevator_warning.status {w['status']!r}")
    items = []
    for it in w["items"]:
        if it["kind"] not in WARNING_KINDS:
            raise ValueError(f"elevator_warning.items[].kind {it['kind']!r}")
        items.append({"station_id": str(it["station_id"]), "station_name": base_name(it["station_name"]),
                      "kind": it["kind"], "message": str(it["message"])})
    return {"status": w["status"], "items": items, "message": str(w["message"])}


def _convert_route(d: dict) -> dict:
    """BE GET /api/routes 응답 → plan_trip이 쓰는 형식 (source.DataSource.route_detail 참고)."""
    path = [str(st["station_id"]) for st in d["stations"]]
    if len(path) < 2:
        raise ValueError("stations가 2개 미만")
    legs, ride, transfer = [], 0, 0
    transfer_ids: List[str] = []
    for step in d["timeline"]:
        minutes = int(step["minutes"])
        if step["kind"] == "RIDE":
            legs.append({"line_id": str(step["line_id"]), "from_id": str(step["from_station"]["station_id"]),
                         "to_id": str(step["to_station"]["station_id"]), "stops": int(step["stops"]),
                         "ride_minutes": minutes})
            ride += minutes
        elif step["kind"] == "TRANSFER":
            transfer_ids.append(str(step["station"]["station_id"]))
            transfer += minutes
        else:
            raise ValueError(f"timeline.kind {step['kind']!r}")
    total = int(d["total_minutes"])
    if not legs or total != ride + transfer:
        raise ValueError("timeline과 total_minutes가 맞지 않음")
    return {
        "found": True, "path": path, "legs": legs, "transfer_station_ids": transfer_ids,
        "transfers": int(d["transfer_count"]), "total_minutes": total,
        "ride_minutes": ride, "transfer_minutes": transfer,
        "elevator_warning": _convert_warning(d["elevator_warning"]),
        "limitations": [x for x in d["limitations"] if isinstance(x, str)],
    }
