"""BE API 데이터 소스 테스트 (src/data/be_source.py, AI 이슈 #4·#5).

픽스처는 BE 데모 DB로 띄운 서버의 실제 응답이다 (tests/data/fixtures/be_demo_responses.json의 _source).
httpx.MockTransport로 BE 서버를 흉내 낸다.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from AI.src.data import DataUnavailableError, use_data_source
from AI.src.data.be_source import BeDataSource, base_name
from AI.src.data.mock_source import MockDataSource
from AI.src.fallback import FallbackEngine
from AI.src.tools import get_broken_elevators, get_elevator_status, plan_trip, resolve_station
from AI.src.tools.elevator import LIMIT_SOME_UNKNOWN, describe_status
from AI.src.tools.registry import execute_tool
from AI.src.tools.trip import (BE_LIMIT_ALREADY_PAST, BE_LIMIT_ELEVATOR_UNCONFIRMED, LIMIT_ESTIMATED,
                               LIMIT_NO_TIMETABLE, LIMIT_SEGMENT_ESTIMATES)

FIX = json.loads((Path(__file__).parent / "fixtures" / "be_demo_responses.json").read_text(encoding="utf-8"))
# POST /api/departure-time 실제 응답 (요청: 도착 2026-12-01 15:00, 이동 20분, 나머지 BE 기본값)
DEP_FIX = json.loads((Path(__file__).parent / "fixtures" / "be_departure_response.json").read_text(encoding="utf-8"))
# GET /api/routes 합성 응답 (계약 §8.4 형식, 실제 녹화 아님 — 파일의 _source 참고). 키: "출발-도착"
ROUTE_FIX = json.loads((Path(__file__).parent / "fixtures" / "be_route_responses.json").read_text(encoding="utf-8"))
ROUTES = ROUTE_FIX["routes"]
NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "요청한 리소스를 찾을 수 없습니다."}}
ROUTE_NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "두 역을 잇는 경로를 찾을 수 없습니다."}}
# BE app/services/status_messages.py (역 ID를 준 출발 시각 요청에서 붙는 문구)
DEP_TRAIN_NOT_FOUND = "시간표에서 도착 희망 시각에 맞는 열차를 찾지 못해 추정 계산을 사용함"
DEP_TRAIN_TRANSFER_UNSUPPORTED = "환승이 필요한 경로는 시간표로 열차를 고르지 않고 추정 계산을 사용함"
WARN_NO_ROUTE_STATIONS = "경로를 찾지 못해 엘리베이터 경고를 확인하지 못함"
VALIDATION_ERROR = {"error": {"code": "VALIDATION_ERROR", "message": "요청 값이 올바르지 않습니다."}}

KST = timezone(timedelta(hours=9))
# 가짜 BE의 서버 시계 (픽스처를 녹화한 날). already_past 판정이 실제 시계에 따라 바뀌지 않게 고정한다.
FAKE_SERVER_NOW = datetime(2026, 11, 14, 12, 0, tzinfo=KST)
DEP_SEGMENT_DEFAULTS = {"walk_to_station_minutes": 7, "station_entry_minutes": 3, "exit_walk_minutes": 3}
DEP_FIELDS = {"arrival_at", "travel_minutes", "buffer_minutes", "from_station_id", "to_station_id", *DEP_SEGMENT_DEFAULTS}
DEP_STEPS = [("WALK_TO_STATION", "집에서 역까지 도보", "walk_to_station_minutes"),
             ("STATION_ENTRY", "역 진입", "station_entry_minutes"),
             ("BUFFER", "여유 시간", "buffer_minutes"),
             ("RIDE", "열차 이동(외부 입력 예상 시간)", "travel_minutes"),
             ("EXIT_WALK", "출구 이동", "exit_walk_minutes")]


def fake_departure(body: dict, server_now: datetime = FAKE_SERVER_NOW) -> httpx.Response:
    """BE POST /api/departure-time을 계약 §8.2 공식으로 흉내 낸다 (녹화한 응답과 같은 모양·문구).

    역 ID를 주면 BE 라우터(app/routers/departure_time.py)처럼 경로 위 엘리베이터 경고를 붙인다. 시간표가 없는
    데모 DB 기준이라 환승 없는 경로는 '맞는 열차 없음', 환승 경로는 '환승 경로' 문구로 추정 계산을 쓴다.
    """
    if set(body) - DEP_FIELDS or "arrival_at" not in body or "travel_minutes" not in body:
        return httpx.Response(422, json=VALIDATION_ERROR)
    ids = (body.get("from_station_id"), body.get("to_station_id"))
    if (ids[0] is None) != (ids[1] is None) or (ids[0] is not None and ids[0] == ids[1]):
        return httpx.Response(422, json=VALIDATION_ERROR)
    sample = DEP_FIX["response"]
    inputs = {"travel_minutes": body["travel_minutes"],
              "buffer_minutes": body["buffer_minutes"] if body.get("buffer_minutes") is not None else 0}
    defaults_applied = []
    for key, default in DEP_SEGMENT_DEFAULTS.items():
        if body.get(key) is None:
            defaults_applied.append(key)
        inputs[key] = default if body.get(key) is None else body[key]
    arrival = datetime.fromisoformat(body["arrival_at"]).astimezone(KST)
    departure = arrival - timedelta(minutes=sum(inputs.values()))
    timeline, cursor = [], departure
    for kind, label, key in DEP_STEPS:
        end = cursor + timedelta(minutes=inputs[key])
        timeline.append({"kind": kind, "label": label, "start_at": cursor.isoformat(),
                         "end_at": end.isoformat(), "minutes": inputs[key]})
        cursor = end
    already_past = departure < server_now
    elevator_msg = sample["elevator_warning"]["message"]
    limitations = [x for x in sample["limitations"] if x != elevator_msg]
    if already_past:
        limitations.append(BE_LIMIT_ALREADY_PAST)
    ride = timeline[3]
    warning = {"status": "UNCONFIRMED", "items": [], "message": elevator_msg}
    if ids[0] is not None:
        route = ROUTES.get(f"{ids[0]}-{ids[1]}")
        if route is None:
            warning = {"status": "UNCONFIRMED", "items": [], "message": WARN_NO_ROUTE_STATIONS}
        else:
            warning = route["elevator_warning"]
            limitations.append(DEP_TRAIN_TRANSFER_UNSUPPORTED if route["transfer_count"] else DEP_TRAIN_NOT_FOUND)
    extra = {}
    if ids[0] is not None:
        # 현재 BE는 selected_train을 항상 준다(계약 §8.2). 녹화 응답(2026-11-14)은 그 전 버전이라 이 필드가 없어
        # 녹화 재현 테스트를 위해 역 ID를 준 요청에만 넣는다.
        extra["selected_train"] = None
    return httpx.Response(200, json={
        "recommended_departure_at": departure.isoformat(),
        "estimated_boarding_at": ride["start_at"],
        "estimated_alighting_at": ride["end_at"],
        "arrival_at": arrival.isoformat(),
        "total_minutes": sum(inputs.values()),
        "date_changed": departure.date() != arrival.date(),
        "already_past": already_past,
        "estimated": True,
        "timetable_used": False,
        "inputs": inputs,
        "defaults_applied": defaults_applied,
        "timeline": timeline,
        "elevator_warning": warning,
        **extra,
        "limitations": limitations + ([warning["message"]] if warning["status"] != "CLEAR" else []),
    })


def fake_be(request: httpx.Request) -> httpx.Response:
    if request.method == "POST":
        if request.url.path == "/api/departure-time":
            return fake_departure(json.loads(request.content))
        return httpx.Response(405, json={"error": {"code": "METHOD_NOT_ALLOWED", "message": "x"}})
    parts = request.url.path.strip("/").split("/")  # api, stations, {id}, ...
    if parts == ["api", "routes"]:
        route = ROUTES.get(f"{request.url.params['from']}-{request.url.params['to']}")
        return httpx.Response(200, json=route) if route else httpx.Response(404, json=ROUTE_NOT_FOUND)
    if parts[:2] == ["api", "elevators"]:
        return httpx.Response(200, json=FIX["elevators_status"])
    if parts[:2] == ["api", "stations"] and len(parts) >= 3:
        sid = parts[2]
        if len(parts) == 3:
            body = FIX["stations"].get(sid)
        elif parts[3] == "elevators":
            body = FIX["elevators"].get(sid) or (
                {"station": {"station_id": int(sid)}, "station_status": {}, "limitations": [], "elevators": []}
                if sid in FIX["stations"] else None)
        elif parts[3] == "alternatives":
            body = FIX["alternatives"].get(sid)
        else:
            body = None
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404, json=NOT_FOUND)
    return httpx.Response(404, json=NOT_FOUND)


def make_source(handler=fake_be) -> BeDataSource:
    return BeDataSource(http=httpx.Client(base_url="http://be.test", transport=httpx.MockTransport(handler)))


@pytest.fixture
def be():
    src = make_source()
    with use_data_source(src):
        yield src


def test_base_name_strips_subtitle_and_suffix():
    assert base_name("잠실(송파구청)") == "잠실"
    assert base_name("교대(법원, 검찰청)") == "교대"
    assert base_name("서울역") == "서울"
    assert base_name("강남") == "강남"


def test_catalog_uses_be_ids_and_plain_names(be):
    assert len(be.stations()) == 240
    assert be.station("25")["name"] == "잠실" and be.station("25")["display_name"] == "잠실(송파구청)"
    assert be.station("1")["limitations"] == ["1호선은 일부 구간의 역만 포함되어 있어 전체 구간을 포함하지 않음"]


def test_resolve_station_with_be_names_and_aliases(be):
    assert resolve_station("잠실역").station_id == "25"
    assert resolve_station("서울역").station_id == "1"
    assert resolve_station("고터").station_id == "77"  # AI 별칭 → 이름 → BE ID
    assert resolve_station("판교").coverage == "OUT_OF_COVERAGE"
    assert resolve_station("부평").coverage == "NOT_FOUND"


def test_elevator_status_follows_be_judgment(be):
    st = get_elevator_status("31")  # 강남: 5대 중 4대 STALE, 합성 1대 가동 안됨
    be_status = FIX["elevators"]["31"]["station_status"]
    assert st.overall_status == be_status["status"] == "UNAVAILABLE"
    assert st.data_fresh is be_status["data_fresh"] is False
    assert (st.total_count, st.unknown_count) == (5, 4)
    assert st.limitations == FIX["elevators"]["31"]["limitations"] == [LIMIT_SOME_UNKNOWN]
    assert st.segments[0].type is None and st.segments[0].unknown_reason == "STALE"
    synthetic = [s for s in st.segments if s.status == "UNAVAILABLE"]
    assert synthetic[0].location == "시연용 합성 설비(실제 상태 아님)"
    text = describe_status(st.model_dump())
    assert "5대 중 1대를 이용할 수 없는" in text and "4대는 상태를 확인하지 못했습니다" in text


def test_all_stale_station_is_unknown(be):
    st = get_elevator_status("35")  # 사당: 전부 STALE
    assert st.overall_status == "UNKNOWN" and FIX["elevators"]["35"]["station_status"]["status"] is None
    assert "확인할 수 없습니다" in describe_status(st.model_dump())


def test_line1_station_carries_coverage_limitation(be):
    st = get_elevator_status("1")
    assert "1호선은 일부 구간의 역만 포함되어 있어 전체 구간을 포함하지 않음" in st.limitations


def test_alternatives_come_from_be(be):
    plan = plan_trip("31", "31")
    assert [d.alternative_station_name for d in plan.detours] == ["역삼", "교대", "남부터미널"]
    assert plan.detours[0].stops_away == 1 and plan.detours[0].same_line is True
    assert plan.detours[0].surface_walk_minutes is None  # BE에 도보 시간이 없음
    assert any("검증되지 않음" in x for x in plan.limitations)


def test_route_comes_from_be_route_api(be):
    plan = plan_trip("43", "25", arrive_by="15:00", now=NOW)  # 신도림 → 잠실
    route = ROUTES["43-25"]
    assert plan.is_possible and plan.route_source == "BE"
    assert plan.route.path == [str(s["station_id"]) for s in route["stations"]]
    assert plan.route.total_duration_minutes == route["total_minutes"] == 36
    assert plan.route.transfers == 0 and plan.route.total_stops == 18
    assert [(leg.line_id, leg.from_id, leg.to_id, leg.stops) for leg in plan.route.legs] == [("2", "43", "25", 18)]
    assert plan.estimated is True
    assert "출발역인 신도림역 엘리베이터 상태를 확인하지 못함" in plan.limitations


def test_be_route_request_uses_integer_station_ids():
    seen = []

    def handler(req):
        if req.url.path == "/api/routes":
            seen.append(dict(req.url.params))
        return fake_be(req)

    with use_data_source(make_source(handler)):
        plan_trip("43", "25", now=NOW)
    assert seen == [{"from": "43", "to": "25"}]


def test_be_route_with_transfer_and_route_elevator_warning(be):
    """역삼 → 고속터미널: 2호선 → 교대 환승 → 3호선 (11분 = 2+2 + 환승 5 + 2). 경유 강남은 가동 안됨."""
    calls = []
    with use_data_source(recording_source(calls)):
        plan = plan_trip("30", "77", arrive_by="15:00", now=NOW)
    route = ROUTES["30-77"]
    assert plan.is_possible and plan.route_source == "BE" and plan.time_source == "BE"
    assert plan.route.path == ["30", "31", "32", "77"]
    assert plan.route.transfers == 1 and plan.route.transfer_station_ids == ["32"]
    assert [(leg.line_id, leg.from_id, leg.to_id, leg.stops, leg.ride_minutes) for leg in plan.route.legs] == \
        [("2", "30", "32", 2, 4), ("3", "32", "77", 1, 2)]
    assert (plan.breakdown.subway_ride, plan.breakdown.transfer_walk) == (6, 5)
    assert plan.route.total_duration_minutes == route["total_minutes"] == 11

    # D17: 경로 소요 시간을 travel_minutes로, 역 ID(정수)를 함께 보낸다
    assert calls[0]["travel_minutes"] == 11
    assert (calls[0]["from_station_id"], calls[0]["to_station_id"]) == (30, 77)
    # 15:00 − (7 + 3 + 5 + 11 + 3) = 14:31
    assert (plan.departure_time, plan.board_time, plan.arrival_time) == ("14:31", "14:46", "15:00")

    # 엘리베이터 경고: 역 ID는 문자열, 역명은 기본 이름. BE 판정을 바꾸지 않는다.
    w = plan.elevator_warning
    assert w.status == "WARNING" and w.message == route["elevator_warning"]["message"]
    assert [(i.station_id, i.station_name, i.kind) for i in w.items] == \
        [("30", "역삼", "UNCONFIRMED"), ("31", "강남", "UNAVAILABLE"), ("32", "교대", "UNCONFIRMED"),
         ("77", "고속터미널", "UNCONFIRMED")]
    assert "경로 위 경유역 강남역: 확인된 엘리베이터가 모두 가동 안됨." in plan.warnings
    assert "환승역인 교대역 엘리베이터 상태를 확인하지 못함" in plan.limitations
    assert w.message in plan.limitations

    # BE limitations는 문구를 바꾸지 않고 그대로 전한다 (출발 시각 → 경로 순)
    for x in route["limitations"] + [DEP_TRAIN_TRANSFER_UNSUPPORTED]:
        assert x in plan.limitations
    assert LIMIT_ESTIMATED not in plan.limitations
    assert plan.timetable_used is False and plan.selected_train is None


def test_be_route_not_found_is_impossible_without_estimate():
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path))
        return fake_be(req)

    with use_data_source(make_source(handler)):
        plan = plan_trip("77", "25", arrive_by="15:00", now=NOW)  # 고속터미널 → 잠실: 가짜 BE에 경로 없음 (404)
    assert ("GET", "/api/routes") in seen
    assert not any(method == "POST" for method, _ in seen)  # 출발 시각을 계산하지 않는다
    assert plan.is_possible is False and plan.route_source == "BE"
    assert plan.warnings[0] == "고속터미널역→잠실역은 두 역을 잇는 경로를 찾지 못해 경로·출발 시각을 안내할 수 없습니다."
    assert plan.route.legs == [] and plan.elevator_warning is None
    assert LIMIT_ESTIMATED not in plan.limitations and LIMIT_NO_TIMETABLE not in plan.limitations


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("timeline"),
    lambda r: r.update(total_minutes=r["total_minutes"] + 1),        # timeline 합과 다름
    lambda r: r["elevator_warning"].update(status="OK"),             # 계약 밖 상태
    lambda r: r["timeline"][0].update(kind="WALK"),
])
def test_malformed_be_route_is_data_unavailable(mutate):
    def handler(req):
        if req.url.path == "/api/routes":
            body = json.loads(json.dumps(ROUTES["43-25"]))
            mutate(body)
            return httpx.Response(200, json=body)
        return fake_be(req)

    with use_data_source(make_source(handler)):
        with pytest.raises(DataUnavailableError):
            plan_trip("43", "25", now=NOW)


def test_route_api_failure_is_data_unavailable():
    def handler(req):
        if req.url.path == "/api/routes":
            return httpx.Response(500, json={"error": {"code": "INTERNAL_ERROR", "message": "x"}})
        return fake_be(req)

    with use_data_source(make_source(handler)):
        out = execute_tool("plan_trip", {"start_id": "43", "end_id": "25", "arrive_by": "15:00"}, now=NOW)
    assert out.get("data_unavailable") is True and "departure_time" not in out


def test_ai_time_constants_match_be_departure_defaults():
    """AI 시간 상수가 BE 기본값과 같은지 (BE app/departure_defaults.py, 계약 §8.2 D15: 도보 7·진입 3·출구 3·대기 5).

    AI는 값을 명시해서 보내므로 BE 기본값이 바뀌어도 안내는 바뀌지 않는다. 바뀌면 이 테스트와 문서를 함께 고친다.
    """
    t = MockDataSource().timing()
    assert (t["home_walk"], t["station_entry_elevator"], t["exit_elevator"]) == (7, 3, 3)
    assert t["headway_line2"] + t["safety_margin"] == 5
    # BE 경로 가중치(역 간 2분)와 Mock 역간 이동 시간도 같다 (환승 5분은 Mock에 없음)
    assert t["minutes_per_stop"] == 2


def test_broken_list_groups_by_station(be):
    res = get_broken_elevators()
    assert {o.station_name for o in res.outages} == {"강남", "선릉"}
    assert res.limitations == ["일부 설비의 상태 정보가 없거나 오래되어 현재 상태를 확인하지 못함"]


def test_fallback_engine_works_on_be_source(be):
    resp = FallbackEngine.process("강남역 엘리베이터 돼?")
    assert resp.card_type == "elevator_status"
    assert "역삼역" in resp.text and f"※ {LIMIT_SOME_UNKNOWN}" in resp.text


@pytest.mark.parametrize("handler", [
    lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused", request=req)),
    lambda req: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=req)),
    lambda req: httpx.Response(500, json={"error": {"code": "INTERNAL_ERROR", "message": "x"}}),
    lambda req: httpx.Response(200, text="not json"),
])
def test_be_failures_raise_data_unavailable(handler):
    src = make_source(handler)
    with pytest.raises(DataUnavailableError):
        src.stations()


def test_unknown_station_id_is_rejected(be):
    with pytest.raises(ValueError):
        get_elevator_status("999999")


# --- 출발 시각: BE POST /api/departure-time (AI 이슈 #6) ---

NOW = datetime(2026, 12, 1, 13, 0)  # 벤치마크처럼 고정한 기준 시각 (KST, naive)
TIME_FIELDS = ("departure_time", "board_time", "arrival_time", "minutes_from_now", "is_late")


def recording_source(calls: list, post_handler=None) -> BeDataSource:
    """POST 본문을 calls에 모으는 BE 소스. post_handler를 주면 POST 응답을 그것으로 바꾼다."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            calls.append(json.loads(req.content))
            if post_handler is not None:
                return post_handler(req)
        return fake_be(req)
    return make_source(handler)


def mock_plan(start: str, end: str, **kw):
    with use_data_source(MockDataSource()):
        return plan_trip(start, end, **kw)


def test_fake_departure_reproduces_recorded_sample():
    """가짜 BE가 녹화한 실제 응답과 같은 값·모양을 내는지 (가짜를 믿을 근거)."""
    assert fake_departure(DEP_FIX["request"]).json() == DEP_FIX["response"]


def test_departure_estimate_parses_recorded_sample():
    calls = []
    src = recording_source(calls, lambda req: httpx.Response(200, json=DEP_FIX["response"]))
    assert src.departure_estimate("2026-12-01T15:00:00+09:00", 20, 0, 7, 3, 3) == DEP_FIX["response"]
    assert calls == [{"arrival_at": "2026-12-01T15:00:00+09:00", "travel_minutes": 20, "buffer_minutes": 0,
                      "walk_to_station_minutes": 7, "station_entry_minutes": 3, "exit_walk_minutes": 3}]
    # plan_trip은 BE가 준 시각을 HH:MM으로 옮긴다 (녹화 응답: 14:27 출발, 14:37 탑승, 15:00 도착)
    with use_data_source(src):
        plan = plan_trip("43", "25", arrive_by="15:00", now=NOW)
    assert (plan.departure_time, plan.board_time, plan.arrival_time) == ("14:27", "14:37", "15:00")
    assert plan.time_source == "BE" and plan.minutes_from_now == 87


@pytest.mark.parametrize("now, arrive_by, home_walk", [
    (NOW, "15:00", 7),
    (NOW, "15:00", 12),
    (datetime(2026, 12, 1, 14, 50), "15:00", 7),   # 이미 늦음
    (datetime(2026, 12, 1, 16, 0), "15:00", 7),    # 지난 시각 → 다음 날
    (datetime(2026, 12, 1, 23, 50), "00:20", 7),   # 자정을 넘는 역산
])
def test_be_departure_equals_mock_local_calculation(now, arrive_by, home_walk):
    calls = []
    src = recording_source(calls)
    with use_data_source(src):
        be_plan = plan_trip("43", "25", arrive_by=arrive_by, home_walk_min=home_walk, now=now)  # 신도림 → 잠실
    local = mock_plan("sindorim", "jamsil", arrive_by=arrive_by, home_walk_min=home_walk, now=now)

    assert be_plan.time_source == "BE" and local.time_source == "AI"
    assert {f: getattr(be_plan, f) for f in TIME_FIELDS} == {f: getattr(local, f) for f in TIME_FIELDS}
    assert be_plan.breakdown == local.breakdown
    # AI 값을 모두 명시해서 보낸다 (BE 기본값, 특히 여유 시간 0분에 맡기지 않음)
    assert len(calls) == 1
    body = calls[0]
    assert body["buffer_minutes"] == 5 == local.breakdown.worst_case_wait
    assert body["walk_to_station_minutes"] == home_walk
    assert (body["station_entry_minutes"], body["exit_walk_minutes"]) == (3, 3)
    assert body["travel_minutes"] == be_plan.route.total_duration_minutes == local.breakdown.subway_ride
    assert body["arrival_at"].endswith("+09:00")


def test_be_departure_arrival_at_rolls_to_next_day():
    calls = []
    with use_data_source(recording_source(calls)):
        plan = plan_trip("43", "25", arrive_by="15:00", now=datetime(2026, 12, 1, 16, 0))
    assert calls[0]["arrival_at"] == "2026-12-02T15:00:00+09:00"
    assert plan.is_late is False and plan.minutes_from_now > 0


def test_be_departure_limitations_replace_ai_estimate_phrase():
    with use_data_source(recording_source([])):
        plan = plan_trip("43", "25", arrive_by="15:00", now=NOW)
    be_limits = [x for x in DEP_FIX["response"]["limitations"] if x != BE_LIMIT_ELEVATOR_UNCONFIRMED]
    assert plan.limitations[:len(be_limits)] == be_limits
    assert LIMIT_ESTIMATED not in plan.limitations  # 같은 취지의 BE 문구와 겹치지 않게
    assert BE_LIMIT_ELEVATOR_UNCONFIRMED not in plan.limitations  # 역 상태는 plan_trip이 직접 확인
    assert "출발역인 신도림역 엘리베이터 상태를 확인하지 못함" in plan.limitations  # AI 항목 유지
    assert len(plan.limitations) == len(set(plan.limitations))
    assert plan.estimated is True
    assert not any("엘리베이터 경고" in w for w in plan.warnings)  # 경고를 만들지 않음


def test_be_timetable_used_limitations_are_passed_through_unchanged():
    """BE가 시간표로 열차를 골랐으면(timetable_used=true) BE 문구를 그대로 전하고 AI 추정 문구는 붙이지 않는다."""
    # BE 0.4절 시간표 문구 (app/services/status_messages.py)
    train_limits = ["열차 시간표의 계획 시각으로 탑승 열차를 골랐으며 지연과 실제 운행 여부는 반영되지 않음",
                    "열차 방향은 시간표의 정차 순서로 추정한 것이며 공식 방향 정보로 검증되지 않음"]

    def timetable_server(req):
        data = fake_departure(json.loads(req.content)).json()
        data["timetable_used"] = True
        # 15:00 도착 희망 → 출구 3분 전까지 내리는 열차: 14:16 탑승, 14:50 하차 (계획 시각)
        data["selected_train"] = {"train_no": "2178", "boarding_at": "2026-12-01T14:16:30+09:00",
                                  "alighting_at": "2026-12-01T14:50:10+09:00", "direction_inferred": True}
        data["recommended_departure_at"] = "2026-12-01T14:01:30+09:00"
        data["estimated_boarding_at"] = "2026-12-01T14:16:30+09:00"
        data["estimated_alighting_at"] = "2026-12-01T14:50:10+09:00"
        for step in data["timeline"]:
            if step["kind"] == "RIDE":
                step.update(label="열차 이동(시간표 계획 시각)", minutes=34)
        data["limitations"] = train_limits + [BE_LIMIT_ELEVATOR_UNCONFIRMED]
        return httpx.Response(200, json=data)

    with use_data_source(recording_source([], timetable_server)):
        plan = plan_trip("43", "25", arrive_by="15:00", now=NOW)
    assert plan.limitations[:2] == train_limits
    assert LIMIT_ESTIMATED not in plan.limitations
    assert BE_LIMIT_ELEVATOR_UNCONFIRMED not in plan.limitations
    assert plan.time_source == "BE" and plan.estimated is True
    assert plan.timetable_used is True
    assert plan.selected_train.model_dump() == {"train_no": "2178", "boarding_time": "14:16",
                                                "alighting_time": "14:50", "direction_inferred": True}
    assert (plan.departure_time, plan.board_time) == ("14:01", "14:16")
    # 열차 이동은 BE 타임라인의 RIDE 구간 (입력한 경로 소요 36분이 아님)
    assert (plan.breakdown.subway_ride, plan.breakdown.transfer_walk) == (34, 0)


def test_be_selected_train_null_is_none(be):
    plan = plan_trip("43", "25", arrive_by="15:00", now=NOW)
    assert plan.timetable_used is False and plan.selected_train is None
    # 시간표 행이 없는 데모 DB: BE가 '맞는 열차 없음' 문구를 붙이고 추정 계산을 쓴다. 문구를 그대로 전한다.
    assert DEP_TRAIN_NOT_FOUND in plan.limitations
    assert plan.is_possible is True  # 이 문구만으로 운행 시간 밖이라고 판정하지 않는다 (시간표 없음과 구분 불가)


def test_malformed_selected_train_is_data_unavailable():
    def server(req):
        data = fake_departure(json.loads(req.content)).json()
        data["selected_train"] = {"train_no": "T1"}  # 시각 없음
        return httpx.Response(200, json=data)

    with use_data_source(recording_source([], server)):
        with pytest.raises(DataUnavailableError):
            plan_trip("43", "25", arrive_by="15:00", now=NOW)


def test_be_already_past_uses_ai_clock_not_be_server_clock():
    """BE 서버 시계로 '이미 지났음'이라 해도 AI 기준 시각으로 늦지 않았으면 늦었다고 하지 않는다."""
    late_server = lambda req: fake_departure(json.loads(req.content), server_now=datetime(2099, 1, 1, tzinfo=KST))
    with use_data_source(recording_source([], late_server)):
        plan = plan_trip("43", "25", arrive_by="15:00", now=NOW)
    assert plan.is_late is False and plan.minutes_from_now > 0
    assert BE_LIMIT_ALREADY_PAST not in plan.limitations


@pytest.mark.parametrize("kwargs", [
    {"depart_at": "14:00"},                 # 순방향
    {},                                      # 지금 출발
])
def test_forward_mode_does_not_call_departure_api(kwargs):
    calls = []
    with use_data_source(recording_source(calls)):
        plan = plan_trip("43", "25", now=NOW, **kwargs)
    assert calls == [] and plan.time_source == "AI" and plan.route_source == "BE"
    # BE 경로로 AI가 순방향 계산: Mock 문구("실제 경로 탐색을 쓰지 않은") 대신 시간표 미사용·구간 추정 + BE 경로 문구
    assert LIMIT_ESTIMATED not in plan.limitations
    assert plan.limitations[:2] == [LIMIT_NO_TIMETABLE, LIMIT_SEGMENT_ESTIMATES]
    assert all(x in plan.limitations for x in ROUTES["43-25"]["limitations"])
    local = mock_plan("sindorim", "jamsil", now=NOW, **kwargs)
    assert {f: getattr(plan, f) for f in TIME_FIELDS} == {f: getattr(local, f) for f in TIME_FIELDS}


@pytest.mark.parametrize("start, end", [("31", "31"), ("77", "25")])  # 대체역만 묻기, 경로 미지원
def test_alternative_only_and_impossible_route_do_not_call_departure_api(start, end):
    calls = []
    with use_data_source(recording_source(calls)):
        plan = plan_trip(start, end, arrive_by="15:00", now=NOW)
    assert calls == [] and plan.time_source == "AI"


def test_mock_source_keeps_local_calculation():
    assert MockDataSource().departure_estimate("2026-12-01T15:00:00+09:00", 20, 5, 7, 3, 3) is None
    plan = mock_plan("sindorim", "jamsil", arrive_by="15:00", now=NOW)
    assert plan.time_source == "AI" and plan.limitations[0] == LIMIT_ESTIMATED


@pytest.mark.parametrize("post_handler", [
    lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused", request=req)),
    lambda req: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=req)),
    lambda req: httpx.Response(500, json={"error": {"code": "INTERNAL_ERROR", "message": "x"}}),
    lambda req: httpx.Response(422, json=VALIDATION_ERROR),
    lambda req: httpx.Response(200, text="not json"),
    lambda req: httpx.Response(200, json={"limitations": []}),  # 시각 필드 없음
    lambda req: httpx.Response(200, json={**DEP_FIX["response"], "recommended_departure_at": "14:27"}),
])
def test_departure_api_failure_is_data_unavailable(post_handler):
    src = recording_source([], post_handler)
    with use_data_source(src):
        with pytest.raises(DataUnavailableError):
            plan_trip("43", "25", arrive_by="15:00", now=NOW)
        out = execute_tool("plan_trip", {"start_id": "43", "end_id": "25", "arrive_by": "15:00"}, now=NOW)
    assert out.get("data_unavailable") is True and "departure_time" not in out


def test_alternative_only_does_not_call_route_api():
    """대체역만 묻기(출발=도착)는 BE 경로 API를 부르지 않는다 (BE는 같은 역 경로를 422로 거절)."""
    seen = []

    def handler(req):
        seen.append(req.url.path)
        return fake_be(req)

    with use_data_source(make_source(handler)):
        plan = plan_trip("31", "31")
    assert "/api/routes" not in seen
    assert plan.is_possible and plan.detours and plan.route.path == ["31"]


def test_mock_source_has_no_route_api():
    assert MockDataSource().route_detail("sindorim", "jamsil") is None
    plan = mock_plan("sindorim", "jamsil", now=NOW)
    assert plan.route_source == "AI" and plan.elevator_warning is None
