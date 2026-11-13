"""FallbackEngine 단위 테스트 (시각 파싱, 역 추출, 의도별 응답)."""

import pytest

from AI.src.fallback import FallbackEngine as F


@pytest.mark.parametrize("text, expected", [
    ("3시 반", "15:30"),
    ("오후 4시 20분", "16:20"),
    ("14:45", "14:45"),
    ("오전 9시", "09:00"),
    ("정오", "12:00"),
    ("열한시", "11:00"),
    ("세 시", "15:00"),
    ("7시", "07:00"),
])
def test_parse_korean_time(text, expected):
    assert F.parse_korean_time(text) == expected


@pytest.mark.parametrize("text", ["25:00", "3시간 걸려?", "네 시청역 엘리베이터", "엘리베이터 돼?"])
def test_parse_korean_time_rejects_non_times(text):
    assert F.parse_korean_time(text) is None


def test_extract_mentions_keeps_order_and_prefers_longest_name():
    m = F.extract_mentions("서울대입구에서 신도림까지")
    assert [s["id"] for s in m["stations"]] == ["seoul_univ", "sindorim"]


def test_extract_mentions_seoul_requires_station_suffix():
    assert F.extract_mentions("서울 지하철 엘리베이터")["stations"] == []
    assert [s["id"] for s in F.extract_mentions("서울역 엘리베이터")["stations"]] == ["seoul"]


def test_extract_mentions_does_not_match_prefix_of_longer_station():
    m = F.extract_mentions("강남구청역 엘리베이터")
    assert m["stations"] == []
    assert m["unknown"] == ["강남구청"]


def test_extract_mentions_unsupported_and_unknown():
    assert F.extract_mentions("판교역 엘리베이터")["unsupported"] == ["판교"]
    assert F.extract_mentions("부평역 엘리베이터 돼?")["unknown"] == ["부평"]


def test_process_elevator_available():
    resp = F.process("강남역 엘리베이터 지금 돼?")
    assert resp.metadata.engine == "fallback" and resp.metadata.fallback_used is True
    assert resp.card_type == "elevator_status"
    assert "강남" in resp.text and "정상" in resp.text
    assert [tc.name for tc in resp.tool_calls] == ["resolve_station", "get_elevator_status"]
    assert resp.tool_calls[0].args == {"query": "강남"}


def test_process_alternative_shows_korean_name():
    resp = F.process("사당역 엘리베이터 고장났다는데 어디로 가야 해?")
    assert "방배역" in resp.text
    assert "bangbae" not in resp.text
    assert resp.actions[0].type == "showAlternative"
    assert any(tc.name == "plan_trip" for tc in resp.tool_calls)


def test_process_departure_reverse():
    resp = F.process("신도림에서 강남까지 3시 반까지 가려면 언제 출발해?", current_time="12:00")
    assert resp.card_type == "trip_plan"
    assert "**14:48**" in resp.text   # 15:30 − 42분
    plan_call = next(tc for tc in resp.tool_calls if tc.name == "plan_trip")
    assert plan_call.args == {"start_id": "sindorim", "end_id": "gangnam", "arrive_by": "15:30"}
    assert any(a.type == "openTime" for a in resp.actions)


def test_process_departure_default_origin_is_announced():
    resp = F.process("잠실까지 3시까지 가려면?", current_time="12:00")
    assert "신도림역 기준으로 안내합니다" in resp.text
    assert [tc.args for tc in resp.tool_calls if tc.name == "resolve_station"] == [{"query": "잠실"}]


def test_process_departure_uses_session_origin():
    resp = F.process("잠실까지 3시까지 가려면?", current_time="12:00", session_ctx={"last_station_id": "gangnam"})
    assert resp.text.startswith("강남역에서 잠실역까지")


def test_process_trip_outside_line2_is_reported_not_guessed():
    resp = F.process("서울역에서 강남까지 가는 길 알려줘")
    assert resp.card_type == "trip_unsupported"
    assert "지원하지 않습니다" in resp.text


def test_process_outages():
    resp = F.process("지금 고장난 역 알려줘")
    assert resp.card_type == "outages_list"
    assert [tc.name for tc in resp.tool_calls] == ["get_broken_elevators"]
    assert "총 3곳" in resp.text


def test_process_out_of_coverage_does_not_answer_with_other_station():
    resp = F.process("판교역 엘리베이터 상태는?")
    assert resp.card_type == "out_of_coverage"
    assert "강남" not in resp.text


@pytest.mark.parametrize("text", ["엘리베이터 돼?", "부평역 엘리베이터 돼?"])
def test_process_without_station_asks_instead_of_guessing(text):
    resp = F.process(text)
    assert resp.card_type == "need_station"
    assert resp.tool_calls == []


def test_process_reference_uses_last_station():
    resp = F.process("거기 대체역은?", session_ctx={"last_station_id": "sadang"})
    assert "사당" in resp.text and "방배역" in resp.text


def test_process_greeting():
    assert F.process("안녕").card_type == "help"


def test_reference_as_destination_keeps_direction():
    # 벤치마크 CX09: "신촌에서 그 역까지" — 신촌 출발, 최근 역(시청) 도착
    res = F.process("신촌에서 그 역까지 3시까지 가려면?", current_time="12:00",
                    session_ctx={"last_station_id": "cityhall"})
    plan = [t for t in res.tool_calls if t.name == "plan_trip"][0]
    assert (plan.args["start_id"], plan.args["end_id"]) == ("sinchon", "cityhall")
    assert res.text.startswith("신촌역에서 시청역까지")


def test_reference_as_origin_keeps_direction():
    res = F.process("거기서 강남까지 3시까지 가려면?", current_time="12:00",
                    session_ctx={"last_station_id": "sindorim"})
    plan = [t for t in res.tool_calls if t.name == "plan_trip"][0]
    assert (plan.args["start_id"], plan.args["end_id"]) == ("sindorim", "gangnam")


def test_outages_respect_line_filter():
    # 벤치마크 EL06
    res = F.process("2호선에서 엘리베이터 고장 난 역 목록 보여줘")
    assert res.card_type == "outages_list"
    assert res.tool_calls[0].args == {"line_id": "2"}
    assert res.text.startswith("현재 2호선에서")
    res = F.process("지금 고장난 역 알려줘")
    assert res.tool_calls[0].args == {}
