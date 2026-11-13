"""후처리 환각 검증기 (Hallucination Guard). 명세 §6.1

LLM 문장에 나온 역명("○○역")과 시각이 이번 요청의 근거(도구 인자·결과, 사용자 문장, 기준 시각)에
있는지 확인합니다. 근거에 없는 역명·시각이 하나라도 있으면 통과시키지 않습니다.
통과하지 못한 문장은 오케스트레이터가 도구 결과로 만든 템플릿 문장(safe_text)으로 바꿉니다.

한계:
    - '역'을 붙이지 않고 쓴 역명은 검사하지 않습니다.
    - 시각은 오전/오후를 구분하지 않고 12시간 차이까지 같은 시각으로 봅니다 ("3시" = 15:00).
    - 소요 시간("42분"), 거리 같은 숫자는 검사하지 않습니다.
"""

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set, Tuple

from ..data import get_data_source
from .response_builder import ToolResult

# "○○역" 꼴이지만 역 이름이 아닌 표현 (폴백 엔진 목록 + 안내 문장에 자주 나오는 말)
GENERIC_WORDS = {
    "대체", "다른", "그", "어느", "이", "저", "인접", "근처", "주변", "환승", "출발", "도착",
    "고장", "고장난", "점검중인", "가까운", "지하철", "전철", "해당", "무슨", "어떤", "모든", "전체",
    "각", "이번", "다음", "어디", "아무", "인근", "목적지", "가까운대체", "지원", "미지원", "두",
    "이용", "정차", "열차", "철도", "기차", "출발지", "도착지", "권장", "우회", "추천",
}

# '역'으로 끝나지만 역 이름이 아닌 명사 (예: "해당지역"). 이 말로 끝나는 표기는 검사하지 않습니다.
NON_STATION_NOUNS = ("지역", "구역", "영역", "전역", "권역", "광역", "해역", "통역", "번역", "면역", "무역")

_RX_STATION = re.compile(r"([가-힣A-Za-z0-9]{2,10})역")
_RX_HHMM = re.compile(r"(?<!\d)(\d{1,2})\s*:\s*(\d{2})(?!\d)")
_RX_KOREAN_TIME = re.compile(r"(?<!\d)(\d{1,2})\s*시(?![간청])(?:\s*(\d{1,2})\s*분|\s*(반))?")


@dataclass
class GuardResult:
    ok: bool
    unknown_stations: List[str] = field(default_factory=list)
    unknown_times: List[str] = field(default_factory=list)


def _strings(node) -> Iterable[str]:
    """dict/list 안의 모든 문자열 값."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _strings(v)


def extract_times(text: str) -> List[Tuple[int, int]]:
    """문장 속 시각 (시, 분) 목록. "14:30", "2시 30분", "3시 반", "3시"."""
    found = [(int(h), int(m)) for h, m in _RX_HHMM.findall(text)]
    for m in _RX_KOREAN_TIME.finditer(text):
        minute = 30 if m.group(3) else int(m.group(2) or 0)
        found.append((int(m.group(1)), minute))
    return [(h, mi) for h, mi in found if h <= 24 and mi < 60]


def _evidence(results: List[ToolResult], user_message: str, current_time: Optional[str]) -> Tuple[Set[str], Set[Tuple[int, int]]]:
    src = get_data_source()
    texts = [user_message, current_time or ""]
    for r in results:
        texts += list(_strings(r.args)) + list(_strings(r.result))

    names: Set[str] = set()
    for t in texts:
        st = src.station(t)
        if st:  # 역 ID → 역명
            names.add(st["name"])
        bare = re.sub(r"\s+", "", t)
        if 2 <= len(bare) <= 12:  # 역명·질의 문자열 그대로 (예: "강남", "강남역")
            names.add(re.sub(r"역$", "", bare))
        names.update(m.group(1) for m in _RX_STATION.finditer(t))
    names = {n for n in names if len(n) >= 2}

    times: Set[Tuple[int, int]] = set()
    for t in texts:
        times.update(extract_times(t))
    return names, times


def _station_ok(base: str, names: Set[str]) -> bool:
    if base in GENERIC_WORDS:
        return True
    # 앞말이 붙어 잡힌 경우("인접한강남")를 위해 접미 일치로 봅니다.
    return any(base.endswith(n) for n in names)


def _time_ok(t: Tuple[int, int], times: Set[Tuple[int, int]]) -> bool:
    h, m = t
    return any(m == em and (h % 12) == (eh % 12) for eh, em in times)


def check_text(text: str, results: List[ToolResult], user_message: str = "",
               current_time: Optional[str] = None) -> GuardResult:
    """LLM 문장의 역명·시각이 근거에 모두 있는지 검사합니다."""
    names, times = _evidence(results, user_message, current_time)

    bad_stations = []
    for m in _RX_STATION.finditer(text):
        base = m.group(1)
        if m.group(0).endswith(NON_STATION_NOUNS):
            continue
        if not _station_ok(base, names) and m.group(0) not in bad_stations:
            bad_stations.append(m.group(0))

    bad_times = []
    for t in extract_times(text):
        label = f"{t[0]:02d}:{t[1]:02d}"
        if not _time_ok(t, times) and label not in bad_times:
            bad_times.append(label)

    return GuardResult(ok=not bad_stations and not bad_times,
                       unknown_stations=bad_stations, unknown_times=bad_times)
