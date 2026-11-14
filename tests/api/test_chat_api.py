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


def test_directional_chat_collects_context_across_http_requests():
    """동일 HTTP 세션의 후속 입력이 실제 후보 조회·수집 시각 전달까지 이어진다."""
    api = client()
    def ask(message):
        response = api.post("/api/chat", json={"session_id": "direction-http", "message": message})
        assert response.status_code == 200
        return response.json()
    first = ask("열차 안인데 목적지 역 엘리베이터가 고장났어요")
    assert first["card_type"] == "need_travel_context"
    assert first["actions"] == []
    second = ask("현재 강남, 2호선, 다음 정차역 역삼")
    assert second["card_type"] == "need_travel_context"
    assert "목적지 역" in second["text"] and "고장 역" in second["text"]
    final = ask("목적지 잠실, 고장 역 사당")
    assert final["session_id"] == "direction-http"
    assert final["card_type"] == "directional_alternatives"
    assert "목적지 잠실역, 고장 역 사당역" in final["text"]
    assert "승차 중" in final["text"]
    assert "방배역" in final["text"]
    assert final["metadata"]["engine"] == "fallback"
    assert final["metadata"]["data_updated_at"] is not None
    assert final["metadata"]["data_updated_at"].endswith("+09:00")
    assert final["actions"] == [{"type": "openStation", "label": "방배역 후보 정보", "payload": {"station_id": "bangbae"}}]
    assert final["tool_calls"] == [
        {"name": "get_elevator_status", "args": {"station_id": "sadang"}},
        {"name": "plan_trip", "args": {"start_id": "sadang", "end_id": "sadang"}},
    ]


def test_cors_preflight_allows_configured_origin():
    no_key = OpenAICompatClient(api_key="", base_url="http://unused", model="none", timeout_seconds=1)
    app = create_app(ChatOrchestrator(client=no_key, sessions=SessionStore()),
                     cors_origins=["http://localhost:5500"])
    res = TestClient(app).options("/api/chat", headers={
        "Origin": "http://localhost:5500", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:5500"


def test_cors_default_allows_any_origin():
    res = client().post("/api/chat", json={"message": "안녕"}, headers={"Origin": "null"})
    assert res.headers["access-control-allow-origin"] == "*"
