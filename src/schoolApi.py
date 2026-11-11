"""학교 마인드로직 FactChat 게이트웨이(Claude Sonnet 5 / GPT-4o-mini) 도구 호출 테스트 스크립트.

OpenAI SDK 표준 인터페이스를 통해 4대 도구(Tools) 호출 및 응답 루프를 검증합니다.
"""

import os
import json
from openai import OpenAI
from AI.src.tools import resolve_station, get_elevator_status, plan_trip, get_broken_elevators

# 1. 클라이언트 설정
# 환경 변수 FACTCHAT_API_KEY가 있으면 우선 사용, 없으면 직접 입력
API_KEY = os.getenv("FACTCHAT_API_KEY", "YOUR_API_KEY")
BASE_URL = os.getenv("FACTCHAT_BASE_URL", "https://factchat-cloud.mindlogic.ai/v1/gateway")
MODEL_NAME = os.getenv("FACTCHAT_MODEL", "claude-sonnet-5")

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
)

# 2. 지하철 도구 명세 (Function Calling Tools)
tools = [
    {
        "type": "function",
        "function": {
            "name": "resolve_station",
            "description": "사용자가 입력한 지하철역 명칭을 정규화하여 고유 역 ID로 변환합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "지하철역 이름 (예: 강남역, 시청, 홍대)"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_elevator_status",
            "description": "특정 역의 엘리베이터 가동 현황(정상/일부고장/전체고장)을 구간 단위로 조회합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "station_id": {"type": "string", "description": "조회할 역의 고유 ID (예: gangnam, cityhall)"}
                },
                "required": ["station_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "plan_trip",
            "description": "출발역과 도착역 간 최적 경로, 엘리베이터 고장 우회(대체역), 보수적 출발 시각을 계산합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_id": {"type": "string", "description": "출발역 고유 ID"},
                    "end_id": {"type": "string", "description": "도착역 고유 ID"},
                    "arrive_by": {"type": "string", "description": "도착 희망 시각 (HH:MM)"}
                },
                "required": ["start_id", "end_id"]
            }
        }
    }
]


def execute_tool(name: str, args: dict) -> dict:
    """도구 이름과 인자를 받아 실제 AI tools 모듈의 함수를 실행합니다."""
    if name == "resolve_station":
        res = resolve_station(args.get("query", ""))
        return res.model_dump()
    elif name == "get_elevator_status":
        res = get_elevator_status(args.get("station_id", ""))
        return res.model_dump()
    elif name == "plan_trip":
        res = plan_trip(
            start_id=args.get("start_id", "sindorim"),
            end_id=args.get("end_id", "sindorim"),
            arrive_by=args.get("arrive_by")
        )
        return res.model_dump()
    return {"error": f"알 수 없는 도구: {name}"}


def test_school_api_tool_calling():
    print(f"🚀 [학교 AI 게이트웨이] 모델: {MODEL_NAME} 도구 호출 테스트 시작...")

    if API_KEY == "YOUR_API_KEY":
        print("\n⚠️ [안내] API_KEY가 설정되지 않았습니다.")
        print("터미널에서 `$env:FACTCHAT_API_KEY=\"발급받은키\"`를 입력하거나,")
        print("파일 상단의 API_KEY 변수에 실제 키를 넣고 실행해 주세요.\n")
        return

    messages = [
        {
            "role": "system",
            "content": (
                "당신은 수도권 지하철 교통약자(휠체어/유모차)를 위한 AI 비서 '우왕이'입니다. "
                "스스로 상태나 경로를 추측하지 말고 반드시 제공된 도구를 호출하여 확인한 뒤 답변하세요."
            )
        },
        {"role": "user", "content": "강남역 지금 휠체어로 엘리베이터 탈 수 있어?"}
    ]

    try:
        # 1차 호출 (도구 호출 요청)
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )

        msg = response.choices[0].message

        if msg.tool_calls:
            print(f"🎉 [성공] {MODEL_NAME} 모델이 도구 호출(Function Calling)을 수행했습니다!")
            messages.append(msg)

            for idx, tc in enumerate(msg.tool_calls, 1):
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments)
                print(f"\n  [도구 호출 {idx}] {fn_name}({fn_args})")

                # 실제 도구 실행
                result = execute_tool(fn_name, fn_args)
                print(f"  👉 실행 결과: {result.get('overall_status') or result.get('station_id') or result}")

                # 도구 실행 결과를 대화 문맥에 주입
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": fn_name,
                    "content": json.dumps(result, ensure_ascii=False)
                })

            # 2차 호출 (최종 자연어 답변)
            print("\n🤖 [2차 호출] 도구 결과를 바탕으로 최종 사용자 안내 문장 생성 중...")
            final_res = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages
            )
            print("\n✅ [우왕이 최종 답변]:")
            print(final_res.choices[0].message.content)

        else:
            print("⚠️ [텍스트 응답] 도구를 호출하지 않고 일반 텍스트로 답했습니다:")
            print(msg.content)

    except Exception as e:
        print(f"❌ [에러 발생]: {e}")


if __name__ == "__main__":
    test_school_api_tool_calling()
