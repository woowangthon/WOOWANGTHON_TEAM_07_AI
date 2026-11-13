"""FastAPI 앱 테스트 (src/api/app.py). 실제 LLM 대신 키 없는 클라이언트로 폴백 경로를 씁니다."""

from fastapi.testclient import TestClient

from AI.src.api.app import create_app
from AI.src.llm.client import OpenAICompatClient
from AI.src.llm.orchestrator import ChatOrchestrator
from AI.src.llm.session import SessionStore


def client():
    no_key = OpenAICompatClient(api_key="", base_url="http://unused", model="none", timeout_seconds=1)
    return TestClient(create_app(ChatOrchestrator(client=no_key, sessions=SessionStore())))


def test_health():
    assert client().get("/health").json() == {"status": "ok"}


def test_chat_returns_chat_response_shape():
    res = client().post("/api/chat", json={"session_id": "s1", "message": "사당역 엘리베이터 돼?", "current_time": "14:15"})
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"session_id", "text", "card_type", "actions", "tool_calls", "suggestions", "metadata"}
    assert body["session_id"] == "s1"
    assert body["metadata"]["engine"] == "fallback"
    assert body["metadata"]["fallback_reason"] == "not_configured"
    assert body["card_type"] == "elevator_status"


def test_chat_rejects_bad_time_format():
    res = client().post("/api/chat", json={"message": "강남역", "current_time": "3시"})
    assert res.status_code == 422


def test_chat_requires_message():
    assert client().post("/api/chat", json={"session_id": "s1"}).status_code == 422
