"""FastAPI 앱: POST /api/chat, GET /health. 명세 §4

실행 (AI/의 부모 폴더 또는 AI/ 안에서):
    python AI/scripts/run_server.py            # http://127.0.0.1:8001
    uvicorn AI.src.api.app:app --port 8001     # 부모 폴더에서

import만으로는 LLM을 호출하지 않습니다. 오케스트레이터는 첫 요청 때 만듭니다.
"""

from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import settings

from ..models import ChatRequest, ChatResponse


def create_app(orchestrator=None, cors_origins: Optional[list] = None) -> FastAPI:
    """앱을 만듭니다. 테스트에서는 가짜 LLM을 넣은 ChatOrchestrator를 넘깁니다.

    cors_origins: FE 출처 목록. None이면 설정 CORS_ALLOW_ORIGINS (기본 "*").
    """
    app = FastAPI(title="우왕이 AI 챗봇 API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins if cors_origins is not None else settings.cors_origins(),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    state: dict = {"orchestrator": orchestrator}

    def get_orchestrator():
        if state["orchestrator"] is None:
            from ..llm.orchestrator import ChatOrchestrator
            state["orchestrator"] = ChatOrchestrator()
        return state["orchestrator"]

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/api/chat", response_model=ChatResponse)
    def chat(req: ChatRequest) -> ChatResponse:
        # 동기 함수라 FastAPI가 스레드 풀에서 실행합니다 (LLM 호출이 이벤트 루프를 막지 않음).
        return get_orchestrator().handle(req)

    return app


app: Optional[FastAPI] = create_app()
