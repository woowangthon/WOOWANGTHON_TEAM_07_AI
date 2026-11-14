"""BE API 데이터 소스 테스트 (src/data/be_source.py, AI 이슈 #4·#5).

픽스처는 BE 데모 DB로 띄운 서버의 실제 응답이다 (tests/data/fixtures/be_demo_responses.json의 _source).
httpx.MockTransport로 BE 서버를 흉내 낸다.
"""

import json
from pathlib import Path

import httpx
import pytest

from AI.src.data import DataUnavailableError, use_data_source
from AI.src.data.be_source import BeDataSource, base_name
from AI.src.fallback import FallbackEngine
from AI.src.tools import get_broken_elevators, get_elevator_status, plan_trip, resolve_station
from AI.src.tools.elevator import LIMIT_SOME_UNKNOWN, describe_status

FIX = json.loads((Path(__file__).parent / "fixtures" / "be_demo_responses.json").read_text(encoding="utf-8"))
NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "요청한 리소스를 찾을 수 없습니다."}}


def fake_be(request: httpx.Request) -> httpx.Response:
    parts = request.url.path.strip("/").split("/")  # api, stations, {id}, ...
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


def test_route_uses_mock_line2_by_name_and_is_estimated(be):
    plan = plan_trip("43", "25", arrive_by="15:00")  # 신도림 → 잠실
    assert plan.is_possible and plan.route.path[0] == "43" and plan.route.path[-1] == "25"
    assert plan.estimated is True
    assert "출발역인 신도림역 엘리베이터 상태를 확인하지 못함" in plan.limitations
    assert plan_trip("77", "25").is_possible is False  # 고속터미널은 2호선 순환선 밖


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
