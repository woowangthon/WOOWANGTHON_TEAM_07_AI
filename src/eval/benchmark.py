"""50문항 벤치마크. 명세 §8

평가셋(cases.json)의 각 문항을 ChatOrchestrator로 실행하고, 마지막 문장의 응답을 채점합니다.

    engine="fallback"  LLM 없이 폴백 엔진만 (키 불필요, 기준선)
    engine="llm"       설정된 LLM 게이트웨이 (AI/.env 키 필요). 실패한 요청은 폴백으로 답하며 따로 셉니다.

채점 항목 (문항마다):
    tools   도구 선택: 필수 도구(tools)를 모두 부르고, tools_any 중 하나 이상을 부르고,
            허용 목록(tools + optional_tools + resolve_station) 밖의 도구는 부르지 않음
    slots   슬롯 추출: 도구 인자를 의미 단위 슬롯으로 바꿔 기대값과 비교 (전체 합산 정밀도·재현율·F1)
    card    card_type이 기대 목록 중 하나
    text    text_includes를 모두 포함하고 text_excludes를 하나도 포함하지 않음
    pass    위 네 가지 + 슬롯 완전 일치
"""

import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from ..llm.client import LLMUnavailableError
from ..llm.orchestrator import ChatOrchestrator
from ..llm.session import SessionStore
from ..models import ChatRequest, ToolCallRecord

CASES_PATH = Path(__file__).with_name("cases.json")
CATEGORIES = ["elevator", "alternative", "departure", "context", "coverage"]
CATEGORY_LABELS = {
    "elevator": "엘리베이터 상태", "alternative": "대체역·우회", "departure": "출발 시각 역산",
    "context": "문맥 연속 대화", "coverage": "커버리지 밖",
}


def load_cases(path: Path = CASES_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def slots_from_calls(calls: Iterable[ToolCallRecord]) -> Set[str]:
    """도구 호출 인자 → 의미 단위 슬롯.

    - get_elevator_status(station_id) / plan_trip(start=end)  → station:<ID>
    - plan_trip(start≠end) → origin:, destination:, arrive_by:, depart_at:
    - get_broken_elevators(line_id) → line:<노선>
    resolve_station 질의 문자열은 슬롯으로 보지 않습니다 (표기가 자유로움).
    """
    slots: Set[str] = set()
    for c in calls:
        a = c.args or {}
        if c.name == "get_elevator_status" and a.get("station_id"):
            slots.add(f"station:{a['station_id']}")
        elif c.name == "plan_trip":
            start, end = a.get("start_id"), a.get("end_id")
            if start and start == end:
                slots.add(f"station:{start}")
                continue
            if start:
                slots.add(f"origin:{start}")
            if end:
                slots.add(f"destination:{end}")
            for key in ("arrive_by", "depart_at"):
                if a.get(key):
                    slots.add(f"{key}:{a[key]}")
        elif c.name == "get_broken_elevators" and a.get("line_id"):
            slots.add(f"line:{a['line_id']}")
    return slots


@dataclass
class CaseResult:
    id: str
    category: str
    message: str
    engine: str
    fallback_reason: Optional[str]
    guard_triggered: bool
    latency_ms: int
    card_type: Optional[str]
    tools_called: List[str]
    expected_slots: List[str]
    predicted_slots: List[str]
    tools_ok: bool
    card_ok: bool
    text_ok: bool
    text: str
    problems: List[str] = field(default_factory=list)

    @property
    def slots_exact(self) -> bool:
        return set(self.expected_slots) == set(self.predicted_slots)

    @property
    def passed(self) -> bool:
        return self.tools_ok and self.card_ok and self.text_ok and self.slots_exact


def score_case(case: dict, resp) -> CaseResult:
    """응답 하나를 기대값과 비교합니다."""
    exp = case["expect"]
    called = [t.name for t in resp.tool_calls]
    called_set = set(called)
    problems: List[str] = []

    required = set(exp.get("tools", []))
    allowed = required | set(exp.get("optional_tools", [])) | {"resolve_station"}
    tools_ok = True
    if not required <= called_set:
        tools_ok = False
        problems.append(f"필수 도구 누락: {sorted(required - called_set)}")
    if exp.get("tools_any") and not called_set & set(exp["tools_any"]):
        tools_ok = False
        problems.append(f"다음 중 하나 필요: {exp['tools_any']}")
    if called_set - allowed:
        tools_ok = False
        problems.append(f"불필요한 도구: {sorted(called_set - allowed)}")

    expected_slots = sorted(exp.get("slots", []))
    predicted_slots = sorted(slots_from_calls(resp.tool_calls))
    if set(expected_slots) != set(predicted_slots):
        problems.append(f"슬롯 기대 {expected_slots} / 실제 {predicted_slots}")

    card_ok = resp.card_type in exp["card_type"]
    if not card_ok:
        problems.append(f"card_type 기대 {exp['card_type']} / 실제 {resp.card_type}")

    missing = [w for w in exp.get("text_includes", []) if w not in resp.text]
    forbidden = [w for w in exp.get("text_excludes", []) if w in resp.text]
    if missing:
        problems.append(f"문장에 없음: {missing}")
    if forbidden:
        problems.append(f"문장에 있으면 안 됨: {forbidden}")

    return CaseResult(
        id=case["id"], category=case["category"], message=case["turns"][-1],
        engine=resp.metadata.engine, fallback_reason=resp.metadata.fallback_reason,
        guard_triggered=resp.metadata.guard_triggered, latency_ms=resp.metadata.latency_ms,
        card_type=resp.card_type, tools_called=called,
        expected_slots=expected_slots, predicted_slots=predicted_slots,
        tools_ok=tools_ok, card_ok=card_ok, text_ok=not missing and not forbidden,
        text=resp.text, problems=problems,
    )


class _NoLLM:
    """항상 LLM을 쓸 수 없다고 답하는 클라이언트 → 운영과 같은 경로로 폴백 엔진을 탑니다."""

    def complete(self, messages, tools):
        raise LLMUnavailableError("not_configured", "벤치마크 폴백 기준선")


def make_orchestrator(engine: str) -> ChatOrchestrator:
    if engine == "fallback":
        return ChatOrchestrator(client=_NoLLM(), sessions=SessionStore())
    if engine == "llm":
        return ChatOrchestrator(sessions=SessionStore())
    raise ValueError(f"engine은 fallback 또는 llm이어야 합니다: {engine!r}")


def run_cases(cases_doc: dict, orchestrator: ChatOrchestrator,
              only: Optional[Iterable[str]] = None) -> List[CaseResult]:
    """문항마다 새 세션으로 준비 대화를 보내고 마지막 문장을 채점합니다."""
    only = set(only or [])
    now = cases_doc.get("default_current_time", "12:00")
    results = []
    for case in cases_doc["cases"]:
        if only and case["category"] not in only and case["id"] not in only:
            continue
        session_id = f"bench-{case['id']}"
        resp = None
        for message in case["turns"]:
            resp = orchestrator.handle(ChatRequest(
                session_id=session_id, message=message, current_time=case.get("current_time", now)))
        results.append(score_case(case, resp))
    return results


def _prf(results: List[CaseResult]) -> Dict[str, float]:
    tp = sum(len(set(r.expected_slots) & set(r.predicted_slots)) for r in results)
    pred = sum(len(r.predicted_slots) for r in results)
    gold = sum(len(r.expected_slots) for r in results)
    p = tp / pred if pred else 1.0
    rcl = tp / gold if gold else 1.0
    f1 = 2 * p * rcl / (p + rcl) if p + rcl else 0.0
    return {"precision": p, "recall": rcl, "f1": f1}


def _pct(values: List[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]


def summarize(results: List[CaseResult]) -> dict:
    def rate(pred) -> float:
        return sum(1 for r in results if pred(r)) / len(results) if results else 0.0

    latencies = [r.latency_ms for r in results]
    llm_answers = [r for r in results if r.engine == "llm"]
    summary = {
        "cases": len(results),
        "pass_rate": rate(lambda r: r.passed),
        "tool_accuracy": rate(lambda r: r.tools_ok),
        "slot": _prf(results),
        "slot_exact_rate": rate(lambda r: r.slots_exact),
        "card_accuracy": rate(lambda r: r.card_ok),
        "text_ok_rate": rate(lambda r: r.text_ok),
        "llm_answers": len(llm_answers),
        "fallback_answers": len(results) - len(llm_answers),
        "guard_triggered": sum(1 for r in llm_answers if r.guard_triggered),
        "latency_ms": {"p50": _pct(latencies, 0.5), "p95": _pct(latencies, 0.95),
                       "mean": int(statistics.mean(latencies)) if latencies else 0},
        "by_category": {},
    }
    for cat in CATEGORIES:
        rs = [r for r in results if r.category == cat]
        if rs:
            summary["by_category"][cat] = {
                "cases": len(rs),
                "pass": sum(r.passed for r in rs),
                "tools_ok": sum(r.tools_ok for r in rs),
                "slot_f1": _prf(rs)["f1"],
                "card_ok": sum(r.card_ok for r in rs),
                "text_ok": sum(r.text_ok for r in rs),
            }
    return summary


def format_report(summary: dict, results: List[CaseResult], engine: str) -> str:
    """마크다운 결과표."""
    s = summary
    lines = [
        f"### 엔진: `{engine}` — {s['cases']}문항",
        "",
        "| 지표 | 값 | 목표 (명세 §8.2) |",
        "|---|---|---|",
        f"| 통과율 (전 항목 일치) | {s['pass_rate']:.0%} | — |",
        f"| 도구 선택 정확도 | {s['tool_accuracy']:.0%} | 98% 이상 |",
        f"| 슬롯 F1 (정밀도 / 재현율) | {s['slot']['f1']:.2f} ({s['slot']['precision']:.2f} / {s['slot']['recall']:.2f}) | 0.95 이상 |",
        f"| 슬롯 완전 일치 문항 | {s['slot_exact_rate']:.0%} | — |",
        f"| card_type 정확도 | {s['card_accuracy']:.0%} | — |",
        f"| 문장 검사 통과 | {s['text_ok_rate']:.0%} | — |",
        f"| LLM 응답 / 폴백 응답 | {s['llm_answers']} / {s['fallback_answers']} | — |",
        f"| 후처리 검증 발동 (LLM 응답 중) | {s['guard_triggered']} | 검증 후 환각 0% |",
        f"| 응답 시간 P50 / P95 | {s['latency_ms']['p50']} / {s['latency_ms']['p95']} ms | P95 2,000ms 이내 (폴백 100ms) |",
        "",
        "| 분류 | 문항 | 통과 | 도구 | 슬롯 F1 | 카드 | 문장 |",
        "|---|---|---|---|---|---|---|",
    ]
    for cat, c in s["by_category"].items():
        lines.append(f"| {CATEGORY_LABELS[cat]} | {c['cases']} | {c['pass']} | {c['tools_ok']} | "
                     f"{c['slot_f1']:.2f} | {c['card_ok']} | {c['text_ok']} |")
    failed = [r for r in results if not r.passed]
    if failed:
        lines += ["", "실패 문항:", "", "| ID | 질문 | 문제 |", "|---|---|---|"]
        for r in failed:
            problems = "; ".join(r.problems).replace("|", "\\|")
            lines.append(f"| {r.id} | {r.message} | {problems} |")
    return "\n".join(lines) + "\n"


def results_as_json(summary: dict, results: List[CaseResult]) -> dict:
    return {"summary": summary,
            "results": [dict(asdict(r), passed=r.passed, slots_exact=r.slots_exact) for r in results]}
