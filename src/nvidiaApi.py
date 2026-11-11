"""NVIDIA API 게이트웨이 Function Calling 호출 테스트 스크립트 (실험용).

NVIDIA 게이트웨이(https://integrate.api.nvidia.com/v1)의 ``z-ai/glm-5.3-flash`` 모델이
도구를 호출하는지만 확인합니다. ``resolve_station`` 도구 하나만 정의하며,
도구를 실제로 실행하거나 2차 호출을 하지는 않습니다. (전체 왕복은 ``schoolApi.py`` 참고)

실행 (PowerShell):
    $env:NVIDIA_API_KEY = "nvapi-..."
    $env:PYTHONIOENCODING = "utf-8"   # Windows 콘솔에서 이모지 출력 오류 방지
    python AI/src/nvidiaApi.py

주의:
    API 호출 코드가 모듈 최상위에 있어 import만 해도 네트워크 요청이 나갑니다.
    다른 모듈에서 import하지 말고 단독 스크립트로만 실행하세요.
"""

import os
from openai import OpenAI

# 1. 클라이언트 설정 (환경 변수 NVIDIA_API_KEY 또는 직접 입력)
client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.getenv("NVIDIA_API_KEY", "$NVIDIA_API_KEY")
)

# 2. 도구(Function) 정의
tools = [
    {
        "type": "function",
        "function": {
            "name": "resolve_station",
            "description": "지하철역 명칭을 고유 ID로 변환합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "지하철역 이름 (예: 강남역, 시청)"}
                },
                "required": ["query"]
            }
        }
    }
]

# 3. 모델 호출 (tools 및 tool_choice 추가)
try:
    completion = client.chat.completions.create(
        model="z-ai/glm-5.3-flash",
        messages=[
            {"role": "system", "content": "You are a helpful subway assistant. Call tools when a station is mentioned."},
            {"role": "user", "content": "강남역 엘리베이터 상태 알려줘"}
        ],
        tools=tools,
        tool_choice="auto",
        temperature=0.5,
        top_p=1,
        max_tokens=1024,
        stream=False
    )

    message = completion.choices[0].message
    print("=== 전체 응답 객체 ===")
    print(message)

    # 4. 도구 호출 여부 판별
    if message.tool_calls:
        print("\n🎉 [성공] 모델이 도구 호출(tool_calls)을 수행했습니다!")
        for tc in message.tool_calls:
            print(f"- 함수명: {tc.function.name}")
            print(f"- 파라미터: {tc.function.arguments}")
    else:
        print("\n⚠️ [텍스트 응답] 도구를 호출하지 않고 일반 텍스트로 답했습니다:")
        print(message.content)

except Exception as e:
    print(f"\n❌ [오류 발생]: {e}")
    if "401" in str(e):
        print("💡 유효한 NVIDIA API 키가 필요합니다. 터미널에서 아래 명령어로 키를 설정한 뒤 다시 실행해 주세요:")
        print('   $env:NVIDIA_API_KEY="실제발급받은nvapi키"')