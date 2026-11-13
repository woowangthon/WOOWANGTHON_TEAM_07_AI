"""LLM 클라이언트.

오케스트레이터는 LLMClient 인터페이스만 씁니다. 실제 구현은 OpenAI 호환 게이트웨이(FactChat, NVIDIA)를 부르는
OpenAICompatClient이고, 테스트에서는 정해진 답을 돌려주는 가짜 클라이언트를 넣습니다.

LLM을 쓸 수 없는 상황(타임아웃, 429, 네트워크 오류, 키 없음 등)은 모두 LLMUnavailableError로 바꿔 던집니다.
오케스트레이터는 이 예외를 받으면 폴백 엔진으로 전환합니다.
"""

import json
from dataclasses import dataclass, field
from typing import List, Optional, Protocol


@dataclass
class ToolCallRequest:
    """LLM이 요청한 도구 호출 하나."""
    id: str
    name: str
    arguments: str  # JSON 문자열 그대로

    def parsed_args(self) -> dict:
        """인자 JSON을 dict로. 깨진 JSON이면 빈 dict (도구가 인자 오류를 돌려줌)."""
        try:
            value = json.loads(self.arguments or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}


@dataclass
class LLMReply:
    """LLM 응답 한 번. tool_calls가 있으면 도구를 실행하고 다시 부릅니다."""
    content: Optional[str] = None
    tool_calls: List[ToolCallRequest] = field(default_factory=list)

    def as_message(self) -> dict:
        """대화 기록에 다시 넣을 assistant 메시지 (OpenAI Chat Completions 형식)."""
        msg: dict = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in self.tool_calls
            ]
        return msg


class LLMUnavailableError(Exception):
    """LLM을 쓸 수 없음. kind: timeout | rate_limit | network | server_error | api_error | not_configured"""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


class LLMClient(Protocol):
    def complete(self, messages: List[dict], tools: List[dict]) -> LLMReply:
        """메시지와 도구 정의를 보내고 응답 하나를 받습니다. 실패하면 LLMUnavailableError."""


class OpenAICompatClient:
    """OpenAI 호환 Chat Completions 게이트웨이 클라이언트.

    타임아웃은 호출 한 번 기준이며, 재시도는 하지 않습니다 (재시도하면 응답이 늦어지므로 바로 폴백).
    """

    def __init__(self, api_key: str, base_url: str, model: str, timeout_seconds: float):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._client = None  # 첫 호출 때 만듭니다 (import 시 부작용 방지)

    @classmethod
    def from_settings(cls, provider: Optional[str] = None) -> "OpenAICompatClient":
        from ..config import settings
        cfg = settings.provider(provider or settings.LLM_PROVIDER)
        return cls(cfg["api_key"], cfg["base_url"], cfg["model"], settings.LLM_TIMEOUT_SECONDS)

    def _get_client(self):
        if not self.api_key:
            raise LLMUnavailableError("not_configured", "LLM API 키가 설정되지 않았습니다")
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.api_key, base_url=self.base_url,
                timeout=self.timeout_seconds, max_retries=0,
            )
        return self._client

    def complete(self, messages: List[dict], tools: List[dict]) -> LLMReply:
        client = self._get_client()  # 키가 없으면 openai를 import하기 전에 바로 실패
        import openai
        try:
            response = client.chat.completions.create(
                model=self.model, messages=messages, tools=tools, tool_choice="auto",
            )
        except openai.APITimeoutError as e:
            raise LLMUnavailableError("timeout", str(e)) from e
        except openai.RateLimitError as e:
            raise LLMUnavailableError("rate_limit", str(e)) from e
        except openai.APIConnectionError as e:
            raise LLMUnavailableError("network", str(e)) from e
        except openai.APIStatusError as e:
            kind = "server_error" if e.status_code >= 500 else "api_error"
            raise LLMUnavailableError(kind, f"HTTP {e.status_code}") from e
        except openai.OpenAIError as e:
            raise LLMUnavailableError("api_error", str(e)) from e

        if not response.choices:
            raise LLMUnavailableError("api_error", "응답에 choices가 없습니다")
        msg = response.choices[0].message
        calls = [
            ToolCallRequest(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in (msg.tool_calls or [])
        ]
        return LLMReply(content=msg.content, tool_calls=calls)
