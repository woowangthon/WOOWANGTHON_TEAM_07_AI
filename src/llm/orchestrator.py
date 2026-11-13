"""/api/chat 오케스트레이터. 명세 §2, §4

요청 하나를 처리하는 흐름:
    1. 세션 슬롯을 꺼내 시스템 프롬프트에 넣는다.
    2. LLM ↔ 도구 왕복 (최대 LLM_MAX_TOOL_ROUNDS회). 도구는 tools.registry.execute_tool로 실행한다.
    3. card_type·actions·suggestions는 도구 결과로 코드가 조립한다 (response_builder).
    4. LLM 문장을 후처리 검증(guard)하고, 근거 없는 역명·시각이 있으면 템플릿 문장으로 바꾼다.
    5. LLM을 쓸 수 없으면(타임아웃·429·네트워크 오류·키 없음·빈 응답·왕복 초과) 폴백 엔진으로 같은 형식의 응답을 만든다.
    6. 세션 슬롯을 갱신하고 latency_ms를 기록한다.
"""

import json
import logging
import time
from datetime import datetime
from typing import List, Optional, Tuple

from ..config import settings
from ..fallback import FallbackEngine
from ..models import ChatRequest, ChatResponse, ChatResponseMetadata, ToolCallRecord
from ..tools import parse_hhmm
from ..tools.registry import TOOL_SPECS, execute_tool
from .client import LLMClient, LLMUnavailableError, OpenAICompatClient
from .guard import check_text
from .prompts import build_system_prompt
from .response_builder import ToolResult, build_response
from .session import SessionContext, SessionStore, update_session

logger = logging.getLogger(__name__)


class _NeedFallback(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ChatOrchestrator:
    """LLM 도구 호출 루프 + 폴백 + 후처리 검증 + 세션 메모리.

    Args:
        client: LLM 클라이언트. None이면 설정(LLM_PROVIDER)으로 OpenAICompatClient를 만듭니다.
        sessions: 세션 저장소. None이면 SESSION_TTL_MINUTES로 새로 만듭니다.
        max_rounds: LLM ↔ 도구 왕복 최대 횟수. None이면 LLM_MAX_TOOL_ROUNDS.
    """

    def __init__(self, client: Optional[LLMClient] = None, sessions: Optional[SessionStore] = None,
                 max_rounds: Optional[int] = None):
        self.client = client if client is not None else OpenAICompatClient.from_settings()
        self.sessions = sessions if sessions is not None else SessionStore(ttl_minutes=settings.SESSION_TTL_MINUTES)
        self.max_rounds = max_rounds or settings.LLM_MAX_TOOL_ROUNDS

    @staticmethod
    def _now(current_time: Optional[str]) -> datetime:
        now = datetime.now().replace(second=0, microsecond=0)
        if current_time:
            h, m = parse_hhmm(current_time, "current_time")
            now = now.replace(hour=h, minute=m)
        return now

    def handle(self, req: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        now = self._now(req.current_time)
        hhmm = now.strftime("%H:%M")
        ctx = self.sessions.get(req.session_id)

        try:
            resp, results = self._run_llm(req, ctx, now)
        except (LLMUnavailableError, _NeedFallback) as e:
            reason = e.kind if isinstance(e, LLMUnavailableError) else e.reason
            logger.warning("LLM 응답 실패로 폴백 전환: %s", e)
            resp, results = self._fallback(req, ctx, hhmm, reason)
        except Exception:  # 도구·응답 조립 중 예기치 못한 오류도 폴백으로 답한다
            logger.exception("오케스트레이터 내부 오류로 폴백 전환")
            resp, results = self._fallback(req, ctx, hhmm, "internal_error")

        self.sessions.save(update_session(ctx, results, resp.card_type))
        resp.metadata.latency_ms = int((time.perf_counter() - started) * 1000)
        return resp

    # ------------------------------------------------------------------
    def _fallback(self, req: ChatRequest, ctx: SessionContext, hhmm: str,
                  reason: str) -> Tuple[ChatResponse, List[ToolResult]]:
        resp = FallbackEngine.process(req.message, req.session_id, hhmm, ctx.as_fallback_ctx())
        resp.metadata.fallback_reason = reason
        return resp, [ToolResult(tc.name, tc.args, {}) for tc in resp.tool_calls]

    def _run_llm(self, req: ChatRequest, ctx: SessionContext,
                 now: datetime) -> Tuple[ChatResponse, List[ToolResult]]:
        hhmm = now.strftime("%H:%M")
        messages = [
            {"role": "system", "content": build_system_prompt(
                hhmm, ctx.last_station_id, ctx.last_station_name, ctx.route_summary())},
            {"role": "user", "content": req.message},
        ]
        results: List[ToolResult] = []

        for _ in range(self.max_rounds):
            reply = self.client.complete(messages, TOOL_SPECS)
            if not reply.tool_calls:
                text = (reply.content or "").strip()
                if not text:
                    raise _NeedFallback("empty_reply")
                break
            messages.append(reply.as_message())
            for tc in reply.tool_calls:
                args = tc.parsed_args()
                result = execute_tool(tc.name, args, now=now)
                results.append(ToolResult(tc.name, args, result))
                messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
        else:
            raise _NeedFallback("max_rounds")

        built = build_response(results)
        verdict = check_text(text, results, req.message, hhmm)
        if not verdict.ok:
            logger.warning("후처리 검증 실패 — 역명 %s, 시각 %s", verdict.unknown_stations, verdict.unknown_times)
            text = built.safe_text

        metadata = ChatResponseMetadata(engine="llm", fallback_used=False, guard_triggered=not verdict.ok)
        if built.data_updated_at:
            metadata.data_updated_at = built.data_updated_at

        resp = ChatResponse(
            session_id=req.session_id,
            text=text,
            card_type=built.card_type,
            actions=built.actions,
            tool_calls=[ToolCallRecord(name=r.name, args=r.args) for r in results],
            suggestions=built.suggestions,
            metadata=metadata,
        )
        return resp, results
