"""LLM Function Calling용 도구 정의와 실행기.

도구 스키마를 손으로 따로 쓰지 않고 models.py의 *Input 모델에서 생성합니다.
LLM 클라이언트(오케스트레이터, scripts/llm_demo.py)는 이 모듈만 쓰면 됩니다.

    from AI.src.tools.registry import TOOL_SPECS, execute_tool

    client.chat.completions.create(..., tools=TOOL_SPECS)
    result = execute_tool(tool_call.function.name, json.loads(tool_call.function.arguments))
"""

from typing import Any, Dict, List, Type

from pydantic import BaseModel, ValidationError

from ..models import (
    BrokenElevatorsInput, ElevatorStatusInput, PlanTripInput, ResolveStationInput,
)
from .elevator import get_broken_elevators, get_elevator_status
from .station import resolve_station
from .trip import plan_trip

# 이름 → (입력 모델, 구현 함수, LLM에 보여줄 설명)
_TOOLS: Dict[str, tuple] = {
    "resolve_station": (
        ResolveStationInput, resolve_station,
        "사용자가 말한 지하철역 이름을 고유 역 ID로 정규화하고 서비스 지원 여부(1~8호선)를 판정합니다. "
        "역 이름이 나오면 다른 도구보다 먼저 호출하세요.",
    ),
    "get_elevator_status": (
        ElevatorStatusInput, get_elevator_status,
        "특정 역의 엘리베이터 가동 현황을 구간(출구/승강장/환승통로) 단위로 조회합니다. "
        "데이터 기준 시각(updated_at)을 함께 반환합니다.",
    ),
    "get_broken_elevators": (
        BrokenElevatorsInput, get_broken_elevators,
        "현재 엘리베이터가 고장 또는 점검 중인 역 목록을 반환합니다. line_id를 생략하면 전체 노선을 조회합니다.",
    ),
    "plan_trip": (
        PlanTripInput, plan_trip,
        "출발역과 도착역 사이 경로, 엘리베이터 고장 시 대체역, 교통약자 기준 보수적 출발/도착 시각을 계산합니다. "
        "arrive_by를 주면 출발 시각을 역산합니다. 고장 역의 대체역만 알고 싶으면 start_id와 end_id에 같은 역을 넣으세요. "
        "is_possible이 false면 시간 값을 안내하지 말고 warnings를 전달하세요.",
    ),
}


def _clean_schema(node: Any) -> Any:
    """Pydantic JSON Schema를 Function Calling에 맞게 단순화합니다.

    - 'title' 제거
    - Optional 필드의 anyOf [X, null] → X (필수 목록에 없으므로 생략 가능)
    """
    if isinstance(node, dict):
        node = {k: v for k, v in node.items() if k != "title"}
        any_of = node.get("anyOf")
        if isinstance(any_of, list):
            non_null = [s for s in any_of if s.get("type") != "null"]
            if len(non_null) == 1:
                merged = {k: v for k, v in node.items() if k != "anyOf"}
                merged.update(non_null[0])
                node = merged
        if node.get("default", "") is None:
            node.pop("default")
        return {k: _clean_schema(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_clean_schema(v) for v in node]
    return node


def _spec(name: str, model: Type[BaseModel], description: str) -> dict:
    params = _clean_schema(model.model_json_schema())
    params.setdefault("properties", {})
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": params},
    }


# OpenAI 호환 Chat Completions API의 tools 인자에 그대로 넘기는 목록
TOOL_SPECS: List[dict] = [_spec(name, model, desc) for name, (model, _, desc) in _TOOLS.items()]

TOOL_NAMES: List[str] = list(_TOOLS)


def execute_tool(name: str, args: dict) -> dict:
    """LLM이 요청한 도구를 실행하고 JSON 직렬화 가능한 dict를 반환합니다.

    인자 검증 실패, 모르는 도구, 도구 내부 ValueError는 예외 대신
    {"error": "..."}로 돌려줘서 LLM이 스스로 바로잡을 수 있게 합니다.
    """
    if name not in _TOOLS:
        return {"error": f"알 수 없는 도구입니다: {name}. 사용 가능: {', '.join(TOOL_NAMES)}"}
    model, fn, _ = _TOOLS[name]
    try:
        parsed = model.model_validate(args or {})
        result = fn(**parsed.model_dump())
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        return {"error": f"{name} 인자 오류 — {problems}"}
    except ValueError as e:
        return {"error": f"{name} 실행 오류 — {e}"}
    return result.model_dump()

