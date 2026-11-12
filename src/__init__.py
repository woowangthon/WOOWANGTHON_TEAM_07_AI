"""우왕톤 7팀 AI 파트 패키지.

AI/ 의 부모 폴더가 import 경로에 있어야 합니다: ``from AI.src import plan_trip``

    config.py    게이트웨이·타임아웃 설정, AI/.env 자동 로딩
    models.py    도구 입출력·/api/chat 스키마 (FE/BE와의 계약)
    data/        데이터 소스 (현재 Mock)
    tools/       도구 4종 + LLM용 도구 정의/실행기
    llm/         시스템 프롬프트 (Phase 2: 오케스트레이터)
    fallback/    규칙 기반 폴백 엔진
"""

from .fallback import FallbackEngine
from .models import ChatRequest, ChatResponse
from .tools import get_broken_elevators, get_elevator_status, plan_trip, resolve_station
from .tools.registry import TOOL_SPECS, execute_tool

__all__ = [
    "resolve_station",
    "get_elevator_status",
    "get_broken_elevators",
    "plan_trip",
    "TOOL_SPECS",
    "execute_tool",
    "FallbackEngine",
    "ChatRequest",
    "ChatResponse",
]
