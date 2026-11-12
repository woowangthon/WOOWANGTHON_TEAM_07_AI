"""LLM 게이트웨이 Function Calling 왕복 데모 (실험용).

모델이 도구를 제대로 고르고, 도구 결과로 안내 문장을 만드는지 터미널에서 확인합니다.
오케스트레이터(/api/chat)가 아니며 세션 메모리, 환각 검증, 폴백 전환은 하지 않습니다.

흐름: 시스템 프롬프트 + 질문 → 모델이 도구 호출 → tools.registry로 실제 실행 → 결과를 대화에 추가
      → 모델이 더 이상 도구를 부르지 않을 때까지 반복(최대 MAX_ROUNDS) → 최종 답변 출력

실행 (어느 폴더에서든 가능, API 키는 AI/.env 또는 환경 변수):
    python AI/scripts/llm_demo.py                              # FactChat, 기본 질문
    python AI/scripts/llm_demo.py "신도림에서 강남까지 3시까지 가려면?"
    python AI/scripts/llm_demo.py --provider nvidia "사당역 엘리베이터 돼?"
    python AI/scripts/llm_demo.py --model gpt-4o-mini "지금 고장난 역 알려줘"
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# AI/ 의 부모 폴더를 import 경로에 추가 (AI.src 패키지 import용)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# Windows 콘솔(cp949)에서 이모지·한글 출력 오류 방지
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from openai import OpenAI  # noqa: E402

from AI.src.config import settings  # noqa: E402
from AI.src.llm.prompts import build_system_prompt  # noqa: E402
from AI.src.tools.registry import TOOL_SPECS, execute_tool  # noqa: E402

MAX_ROUNDS = 4
DEFAULT_QUESTION = "강남역 지금 휠체어로 엘리베이터 탈 수 있어?"


def run(question: str, provider: str, model_override: str = "") -> int:
    cfg = settings.provider(provider)
    model = model_override or cfg["model"]
    if not cfg["api_key"]:
        key_name = "FACTCHAT_API_KEY" if provider == "factchat" else "NVIDIA_API_KEY"
        print(f"⚠️ {key_name}가 없습니다. AI/.env에 넣거나 환경 변수로 설정하세요. (AI/.env.example 참고)")
        return 1

    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], timeout=30)
    messages = [
        {"role": "system", "content": build_system_prompt(datetime.now().strftime("%H:%M"))},
        {"role": "user", "content": question},
    ]
    print(f"🚀 [{provider}] 모델: {model}\n🙋 질문: {question}")

    try:
        for round_no in range(1, MAX_ROUNDS + 1):
            response = client.chat.completions.create(
                model=model, messages=messages, tools=TOOL_SPECS, tool_choice="auto",
            )
            msg = response.choices[0].message
            if not msg.tool_calls:
                print(f"\n✅ [우왕이 최종 답변]\n{msg.content}")
                return 0

            messages.append(msg.model_dump(exclude_none=True))
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = execute_tool(tc.function.name, args)
                summary = result.get("error") or result.get("overall_status") or result.get("station_id") \
                    or result.get("departure_time") or f"{len(json.dumps(result))} bytes"
                print(f"  [라운드 {round_no}] {tc.function.name}({args}) → {summary}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
        print(f"\n⚠️ 도구 호출이 {MAX_ROUNDS}라운드를 넘어 중단했습니다.")
        return 1
    except Exception as e:  # 데모 스크립트이므로 모든 오류를 출력만 하고 종료
        print(f"\n❌ [오류] {type(e).__name__}: {e}")
        if "401" in str(e):
            print("💡 API 키가 올바른지 확인하세요.")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM 게이트웨이 Function Calling 왕복 데모")
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION, help="사용자 질문")
    parser.add_argument("--provider", choices=["factchat", "nvidia"], default="factchat")
    parser.add_argument("--model", default="", help="모델 이름 (생략 시 설정값)")
    args = parser.parse_args()
    return run(args.question, args.provider, args.model)


if __name__ == "__main__":
    sys.exit(main())
