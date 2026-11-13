"""테스트용 가짜 LLM 클라이언트. 정해진 응답을 순서대로 돌려줍니다."""

import json
from typing import List, Union

from AI.src.llm.client import LLMReply, LLMUnavailableError, ToolCallRequest


def call(name: str, **args) -> ToolCallRequest:
    return ToolCallRequest(id=f"call_{name}", name=name, arguments=json.dumps(args, ensure_ascii=False))


def tools(*calls: ToolCallRequest) -> LLMReply:
    return LLMReply(content=None, tool_calls=list(calls))


def say(text: str) -> LLMReply:
    return LLMReply(content=text)


class FakeLLM:
    """script의 각 항목: LLMReply면 그대로 반환, Exception이면 던집니다."""

    def __init__(self, script: List[Union[LLMReply, Exception]]):
        self.script = list(script)
        self.requests: List[List[dict]] = []

    def complete(self, messages, tools_spec):
        self.requests.append([dict(m) for m in messages])
        if not self.script:
            raise AssertionError("FakeLLM: 준비된 응답보다 많이 호출됨")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def unavailable(kind: str) -> LLMUnavailableError:
    return LLMUnavailableError(kind, "test")
