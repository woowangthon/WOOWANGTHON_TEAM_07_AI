"""50문항 벤치마크 실행. 명세 §8, 결과 기록: docs/BENCHMARK.md

    python AI/scripts/run_benchmark.py                       # 폴백 엔진 기준선 (키 불필요)
    python AI/scripts/run_benchmark.py --engine llm          # LLM 게이트웨이 (AI/.env 키 필요)
    python AI/scripts/run_benchmark.py --only departure CX04 # 분류나 문항 ID로 골라 실행
    python AI/scripts/run_benchmark.py --json out.json       # 문항별 결과 저장

LLM 모드에서 키가 없거나 호출이 실패한 문항은 폴백으로 답하며, 결과표의 "LLM 응답 / 폴백 응답"에 따로 셉니다.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from AI.src.eval.benchmark import (  # noqa: E402
    format_report, load_cases, make_orchestrator, results_as_json, run_cases, summarize,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 챗봇 50문항 벤치마크")
    parser.add_argument("--engine", choices=["fallback", "llm"], default="fallback")
    parser.add_argument("--only", nargs="*", help="분류(elevator 등) 또는 문항 ID")
    parser.add_argument("--json", help="문항별 결과를 저장할 JSON 경로")
    parser.add_argument("--timeout", type=float, help="LLM 호출 1회 타임아웃(초) 덮어쓰기. 생략 시 config 값")
    parser.add_argument("--model", help="LLM 모델 덮어쓰기. 생략 시 config 값")
    parser.add_argument("--preresolve", action="store_true", help="역 사전 확인 켜기 (LLM_PRERESOLVE 덮어쓰기)")
    parser.add_argument("--answer-mode", choices=["llm", "template"], help="답변 문장 방식 (LLM_ANSWER_MODE 덮어쓰기)")
    parser.add_argument("--quiet", action="store_true", help="문항별 진행 표시 끄기")
    args = parser.parse_args()

    logging.basicConfig(level=logging.ERROR)  # 폴백 전환 경고는 결과표에 집계되므로 숨김
    def progress(r):
        if not args.quiet:
            mark = "✅" if r.passed else "❌"
            print(f"{mark} {r.id} {r.engine}{'/' + r.fallback_reason if r.fallback_reason else ''} {r.latency_ms}ms",
                  file=sys.stderr, flush=True)

    orchestrator = make_orchestrator(args.engine, args.timeout, args.model,
                                     True if args.preresolve else None, args.answer_mode)
    results = run_cases(load_cases(), orchestrator, args.only, progress)
    if not results:
        print("실행할 문항이 없습니다.")
        return 1
    summary = summarize(results)
    label = args.engine
    if args.engine == "llm":
        label += (f" / {orchestrator.client.model} / 호출당 타임아웃 {orchestrator.client.timeout_seconds:g}초"
                  f" / 역 사전 확인 {'켬' if orchestrator.preresolve else '끔'} / 답변 {orchestrator.answer_mode}")
    print(format_report(summary, results, label))
    if args.json:
        Path(args.json).write_text(
            json.dumps(results_as_json(summary, results), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
