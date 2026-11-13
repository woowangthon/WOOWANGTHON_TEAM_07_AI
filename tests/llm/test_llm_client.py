"""LLM 클라이언트 메시지 변환 테스트 (src/llm/client.py)."""

from AI.src.llm.client import LLMReply, ToolCallRequest


def test_as_message_keeps_raw_provider_fields():
    # Gemini는 tool_calls의 thought_signature를 다음 호출에 그대로 돌려받아야 한다 (없으면 HTTP 400).
    raw = {"role": "assistant", "tool_calls": [{
        "id": "c1", "type": "function", "function": {"name": "resolve_station", "arguments": "{}"},
        "extra_content": {"google": {"thought_signature": "sig"}}}]}
    reply = LLMReply(tool_calls=[ToolCallRequest("c1", "resolve_station", "{}")], raw_message=raw)
    msg = reply.as_message()
    assert msg["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "sig"
    assert msg is not raw


def test_as_message_without_raw_builds_openai_format():
    reply = LLMReply(tool_calls=[ToolCallRequest("c1", "plan_trip", '{"start_id": "a"}')])
    assert reply.as_message() == {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "plan_trip", "arguments": '{"start_id": "a"}'}}]}
