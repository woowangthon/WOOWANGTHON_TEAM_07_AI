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
    args = parser.parse_args()

    logging.basicConfig(level=logging.ERROR)  # 폴백 전환 경고는 결과표에 집계되므로 숨김
    results = run_cases(load_cases(), make_orchestrator(args.engine), args.only)
    if not results:
        print("실행할 문항이 없습니다.")
        return 1
    summary = summarize(results)
    print(format_report(summary, results, args.engine))
    if args.json:
        Path(args.json).write_text(
            json.dumps(results_as_json(summary, results), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
