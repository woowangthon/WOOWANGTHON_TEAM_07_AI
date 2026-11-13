"""세션 슬롯 메모리. 명세 §5.2

대화 원문은 저장하지 않고 "최근 역·최근 경로·최근 질의 종류" 슬롯만 둡니다.
인메모리 TTL 저장소이며, 만료된 세션은 접근하거나 저장할 때 지웁니다. 서버를 재시작하면 사라집니다.
"""

from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Callable, Iterable, Optional

from pydantic import BaseModel, Field

from ..data import get_data_source, station_name
from ..tools import resolve_station
from .response_builder import ToolResult


class SessionContext(BaseModel):
    session_id: str
    last_station_id: Optional[str] = None
    last_station_name: Optional[str] = None
    last_route: Optional[dict] = None
    last_query_type: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.now)

    def as_fallback_ctx(self) -> dict:
        """FallbackEngine.process(session_ctx=...)에 넘길 dict."""
        return {"last_station_id": self.last_station_id}

    def route_summary(self) -> Optional[str]:
        """시스템 프롬프트에 넣을 최근 경로 요약."""
        r = self.last_route
        if not r:
            return None
        summary = f"{station_name(r['start_id'])}역 → {station_name(r['end_id'])}역"
        if r.get("departure_time") and r.get("arrival_time"):
            summary += f" ({r['departure_time']} 출발, {r['arrival_time']} 도착 예정)"
        return summary


class SessionStore:
    """TTL이 있는 인메모리 세션 저장소.

    Args:
        ttl_minutes: 마지막 갱신 후 이 시간이 지나면 세션을 버립니다.
        max_sessions: 이 수를 넘으면 가장 오래된 세션부터 버립니다 (메모리 상한).
        clock: 현재 시각 함수 (테스트용).
    """

    def __init__(self, ttl_minutes: int = 30, max_sessions: int = 10_000,
                 clock: Callable[[], datetime] = datetime.now):
        self.ttl = timedelta(minutes=ttl_minutes)
        self.max_sessions = max_sessions
        self.clock = clock
        self._items: "OrderedDict[str, SessionContext]" = OrderedDict()

    def __len__(self) -> int:
        return len(self._items)

    def _expired(self, ctx: SessionContext, now: datetime) -> bool:
        return now - ctx.updated_at > self.ttl

    def purge_expired(self) -> int:
        """만료된 세션을 지우고 지운 수를 반환합니다."""
        now = self.clock()
        stale = [sid for sid, ctx in self._items.items() if self._expired(ctx, now)]
        for sid in stale:
            del self._items[sid]
        return len(stale)

    def get(self, session_id: str) -> SessionContext:
        """세션을 돌려줍니다. 없거나 만료됐으면 빈 세션을 새로 만듭니다 (저장은 save에서)."""
        ctx = self._items.get(session_id)
        if ctx is not None and self._expired(ctx, self.clock()):
            del self._items[session_id]
            ctx = None
        return ctx.model_copy() if ctx else SessionContext(session_id=session_id, updated_at=self.clock())

    def save(self, ctx: SessionContext) -> None:
        ctx.updated_at = self.clock()
        self._items[ctx.session_id] = ctx
        self._items.move_to_end(ctx.session_id)
        self.purge_expired()
        while len(self._items) > self.max_sessions:
            self._items.popitem(last=False)


def update_session(ctx: SessionContext, results: Iterable[ToolResult], card_type: Optional[str]) -> SessionContext:
    """이번 요청의 도구 호출로 세션 슬롯을 갱신합니다.

    최근 역 우선순위: 경로의 도착역 > 상태를 조회한 역 > 대체역을 찾은 역 > 마지막으로 확인된 역.
    폴백 응답은 도구 결과 없이 인자만 있으므로(result가 빈 dict) 인자로 판단합니다.
    """
    results = list(results)
    src = get_data_source()
    candidates = []  # (우선순위, 순서, 역 ID)

    for order, r in enumerate(results):
        if "error" in r.result:
            continue
        if r.name == "plan_trip":
            start, end = r.args.get("start_id"), r.args.get("end_id")
            if start and end and start != end:
                candidates.append((3, order, end))
                route = {"start_id": start, "end_id": end, "arrive_by": r.args.get("arrive_by")}
                if r.result:
                    route["departure_time"] = r.result.get("departure_time")
                    route["arrival_time"] = r.result.get("arrival_time")
                ctx.last_route = route
            elif start:
                candidates.append((1, order, start))
        elif r.name == "get_elevator_status" and r.args.get("station_id"):
            candidates.append((2, order, r.args["station_id"]))
        elif r.name == "resolve_station":
            res = r.result or resolve_station(r.args.get("query", "")).model_dump()
            if res.get("coverage") == "SUPPORTED" and res.get("station_id"):
                candidates.append((0, order, res["station_id"]))

    valid = [c for c in candidates if src.station(c[2])]
    if valid:
        sid = max(valid)[2]
        ctx.last_station_id = sid
        ctx.last_station_name = station_name(sid)
    if card_type:
        ctx.last_query_type = card_type
    return ctx
