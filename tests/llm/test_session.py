"""세션 슬롯 메모리 테스트 (src/llm/session.py)."""

from datetime import datetime, timedelta

from AI.src.llm.response_builder import ToolResult
from AI.src.llm.session import SessionContext, SessionStore, update_session


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 13, 12, 0)

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)


def test_session_expires_after_ttl():
    clock = Clock()
    store = SessionStore(ttl_minutes=30, clock=clock)
    store.save(SessionContext(session_id="s", last_station_id="gangnam"))

    clock.advance(29)
    assert store.get("s").last_station_id == "gangnam"
    clock.advance(2)
    assert store.get("s").last_station_id is None
    assert len(store) == 0


def test_expired_sessions_do_not_pile_up():
    clock = Clock()
    store = SessionStore(ttl_minutes=30, clock=clock)
    for i in range(100):
        store.save(SessionContext(session_id=f"old{i}"))
    clock.advance(31)
    store.save(SessionContext(session_id="new"))
    assert len(store) == 1


def test_max_sessions_drops_oldest():
    store = SessionStore(max_sessions=2)
    for sid in ("a", "b", "c"):
        store.save(SessionContext(session_id=sid))
    assert len(store) == 2
    assert store.get("a").last_station_id is None


def test_get_returns_copy_until_saved():
    store = SessionStore()
    ctx = store.get("s")
    ctx.last_station_id = "gangnam"
    assert store.get("s").last_station_id is None


def test_update_prefers_trip_destination():
    ctx = SessionContext(session_id="s")
    update_session(ctx, [
        ToolResult("get_elevator_status", {"station_id": "sadang"}, {"station_id": "sadang"}),
        ToolResult("plan_trip", {"start_id": "sindorim", "end_id": "gangnam", "arrive_by": "15:00"},
                   {"departure_time": "14:18", "arrival_time": "15:00"}),
    ], "trip_plan")
    assert ctx.last_station_id == "gangnam"
    assert ctx.last_station_name == "강남"
    assert ctx.last_query_type == "trip_plan"
    assert ctx.route_summary() == "신도림역 → 강남역 (14:18 출발, 15:00 도착 예정)"


def test_update_from_fallback_records_without_results():
    ctx = SessionContext(session_id="s")
    update_session(ctx, [ToolResult("resolve_station", {"query": "홍대"}, {})], "need_station")
    assert ctx.last_station_id == "hongik"


def test_update_ignores_errors_and_unknown_stations():
    ctx = SessionContext(session_id="s", last_station_id="gangnam")
    update_session(ctx, [
        ToolResult("get_elevator_status", {"station_id": "nowhere"}, {}),
        ToolResult("plan_trip", {"start_id": "x", "end_id": "y"}, {"error": "알 수 없는 역"}),
    ], None)
    assert ctx.last_station_id == "gangnam"
