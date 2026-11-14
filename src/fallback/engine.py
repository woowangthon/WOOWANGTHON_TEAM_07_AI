"""규칙 기반 폴백 엔진 (Fallback Engine).

FE/Prototype-v2/js/ai-agent.js의 규칙 기반 의도 분류를 Python으로 옮긴 모듈입니다.
LLM API 장애, 타임아웃, Rate Limit(429) 시 오케스트레이터가 이 엔진으로 같은 스키마의 응답을 만듭니다.
LLM을 쓰지 않으므로 안내 문장은 모두 도구 결과를 채워 넣는 템플릿입니다.
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..data import get_data_source, station_name
from ..models import ActionPayload, ChatResponse, ChatResponseMetadata, ToolCallRecord
from ..tools import get_broken_elevators, get_elevator_status, parse_hhmm, plan_trip, resolve_station
from ..tools.elevator import describe_status, format_limitations
from ..tools.trip import detour_text


def _station_vocab() -> List[Tuple[str, str]]:
    """문장에서 역 이름을 찾을 때 쓰는 사전: (표기, 역 ID). 긴 표기부터 매칭합니다."""
    src = get_data_source()
    return sorted(
        [(st["name"], sid) for sid, st in src.stations().items()] + list(src.aliases().items()),
        key=lambda x: -len(x[0]),
    )

# "○○역" 꼴이지만 역 이름이 아닌 표현
_NOT_STATION_WORDS = {
    "대체", "다른", "그", "어느", "이", "저", "인접", "근처", "주변", "환승", "출발", "도착",
    "고장", "고장난", "점검중인", "가까운", "지하철", "전철", "해당", "무슨", "어떤", "모든", "전체",
    "각", "이번", "다음", "어디", "아무",
}

_SUGGEST_START = ["강남역 엘리베이터 돼?", "신도림에서 잠실까지 3시까지 가려면?", "지금 고장난 역 알려줘"]


class FallbackEngine:
    """LLM 없이 정규식 규칙만으로 ChatResponse를 만드는 폴백 엔진.

    모든 메서드는 classmethod이므로 인스턴스 없이 FallbackEngine.process(...)로 호출합니다.
    의도 분류 점수표와 시각 파싱 규칙은 AI/docs/IMPLEMENTATION_STATUS.md §3 참고.
    """

    # 출발역을 알 수 없을 때 쓰는 기본 출발역 (시스템 프롬프트 명세 §5.1과 동일)
    DEFAULT_ORIGIN = "신도림"  # 역 이름 (resolve_station으로 ID를 찾음)

    # 의도 판별 정규식 (Prototype-v2 ai-agent.js의 RX 포팅)
    RX_GREETING = re.compile(r"안녕|도움|뭐\s*할\s*수|사용법|도와줘|하이|hello|\bhi\b|help", re.IGNORECASE)
    RX_OUTAGES = re.compile(r"고장\s*난\s*(역|곳|데)|점검\s*중인\s*(역|곳)|어디\s*(가|가요)?\s*고장|현황|전체|목록|리스트")
    RX_ALTERNATIVE = re.compile(r"고장|안\s*돼|안\s*되|못\s*타|못\s*써|대체|우회|다른\s*역|어디로|멈췄|멈춰")
    RX_ELEVATOR = re.compile(r"엘리베이터|엘베|승강기|돼\s*\?|되나|돼요|되요|운행|상태|휠체어|유모차|이용\s*가능|탈\s*수")
    RX_DEPARTURE_STRONG = re.compile(r"출발|몇\s*시에|몇\s*시쯤|언제\s*나가|늦지\s*않게|나가야|나서야|집에서\s*언제")
    RX_DEPARTURE_WEAK = re.compile(r"까지\s*가려면|도착하려면|까지\s*도착|에\s*도착|까지\s*가야")
    RX_ROUTE = re.compile(r"가는\s*길|경로|어떻게\s*가|환승|길\s*(좀\s*)?알려|가는\s*방법|가려면")
    RX_REFERENCE = re.compile(r"거기|그\s*역|그곳|그\s*쪽|그럼|그러면|그\s*데|여기")
    # 지칭어가 도착지로 쓰인 경우 ("신촌에서 그 역까지") — 이때 문장의 다른 역은 출발역입니다.
    RX_REFERENCE_AS_DEST = re.compile(r"(거기|그\s*역|그곳|그\s*쪽|여기)\s*(까지|으로|로)")
    RX_LINE = re.compile(r"(?<!\d)([1-8])\s*호선")

    KOREAN_NUMS = {
        "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6,
        "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "열한": 11, "열두": 12,
    }
    # 긴 표현을 먼저 시도해야 "열한"이 "열"로 잘리지 않습니다.
    # 뒤에 '간'(3시간)이나 '청'(네 시청역)이 오면 시각이 아닙니다.
    _RX_HOUR = re.compile(
        r"(\d{1,2}|열한|열두|다섯|여섯|일곱|여덟|아홉|한|두|세|네|열)\s*시(?![간청])"
        r"(?:\s*(\d{1,2})\s*분|\s*(반))?"
    )

    # -------------------------------------------------------------------------
    # 입력 해석
    # -------------------------------------------------------------------------
    @classmethod
    def parse_korean_time(cls, text: str) -> Optional[str]:
        """한국어 시각 표현을 HH:MM(24시간제)으로 바꿉니다. 시각이 없거나 범위를 벗어나면 None.

        - "14:45" 그대로 / "정오" 12:00 / "자정" 00:00
        - "N시", "N시 M분", "N시 반" (N은 숫자 또는 한~열두)
        - 오후·저녁·밤이면 +12, 오전·아침·새벽 12시는 0시
        - 오전/오후 표시 없이 1~6시면 오후로 간주
        """
        t = text.strip()

        m = re.search(r"(\d{1,2})\s*:\s*(\d{2})", t)
        if m:
            h, minute = int(m.group(1)), int(m.group(2))
            return f"{h:02d}:{minute:02d}" if h < 24 and minute < 60 else None

        if "정오" in t:
            return "12:00"
        if "자정" in t:
            return "00:00"

        m = cls._RX_HOUR.search(t)
        if not m:
            return None
        h_str, m_str, half = m.group(1), m.group(2), m.group(3)
        h = int(h_str) if h_str.isdigit() else cls.KOREAN_NUMS[h_str]
        minute = 30 if half else (int(m_str) if m_str else 0)

        is_pm = bool(re.search(r"오후|저녁|밤", t))
        is_am = bool(re.search(r"오전|아침|새벽", t))
        if is_pm and h < 12:
            h += 12
        elif is_am and h == 12:
            h = 0
        elif not is_am and not is_pm and 1 <= h <= 6:
            h += 12

        return f"{h:02d}:{minute:02d}" if h < 24 and minute < 60 else None

    @classmethod
    def extract_mentions(cls, text: str) -> Dict[str, List]:
        """문장에서 역 언급을 찾습니다.

        Returns:
            {
              "stations": 지원 역 [{"id", "name", "lines"}] (문장 등장 순서, 중복 제거),
              "unsupported": 서비스 범위 밖 키워드 목록 (예: ["판교"]),
              "unknown": "○○역" 꼴인데 사전에 없는 이름 목록 (예: ["부평"]),
            }
        """
        src = get_data_source()
        requiring_suffix = src.names_requiring_suffix()
        out_keywords = src.out_of_coverage_keywords()
        found: List[Tuple[int, str]] = []
        taken = [False] * len(text)
        for word, sid in _station_vocab():
            for m in re.finditer(re.escape(word), text):
                s, e = m.start(), m.end()
                if any(taken[s:e]):
                    continue
                if word in requiring_suffix and not text.startswith("역", e):
                    continue
                # "강남구청역"처럼 더 긴 역 이름의 앞부분이면 다른 역입니다.
                tail = re.match(r"[가-힣A-Za-z0-9]*", text[e:]).group(0)
                if tail.endswith("역") and len(tail) > 1:
                    continue
                for i in range(s, e):
                    taken[i] = True
                found.append((s, sid))

        stations, seen = [], set()
        for _, sid in sorted(found):
            if sid not in seen:
                seen.add(sid)
                st = src.station(sid)
                stations.append({"id": sid, "name": st["name"], "lines": st["lines"]})

        lowered = text.lower()
        unsupported = [kw for kw in out_keywords if kw.lower() in lowered]

        unknown = []
        for m in re.finditer(r"([가-힣A-Za-z0-9]{1,10})역", text):
            if any(taken[m.start():m.end() - 1]):
                continue
            word = m.group(1)
            if word in _NOT_STATION_WORDS or any(kw in word for kw in out_keywords):
                continue
            unknown.append(word)

        return {"stations": stations, "unsupported": unsupported, "unknown": unknown}

    @classmethod
    def classify_intent(cls, text: str, stations: List[Dict[str, Any]], parsed_time: Optional[str]) -> str:
        """점수 기반 규칙으로 의도를 분류합니다.

        반환값: departure | route | alternative | outages | elevator | greeting
        (모든 점수가 0 이하이면 elevator)
        """
        scores = {"departure": 0, "alternative": 0, "outages": 0, "route": 0, "elevator": 0, "greeting": 0}

        if cls.RX_DEPARTURE_STRONG.search(text):
            scores["departure"] += 4
        if cls.RX_DEPARTURE_WEAK.search(text):
            if parsed_time:
                scores["departure"] += 4
            else:
                scores["route"] += 3
        if parsed_time and re.search(r"까지|도착|가려면|에\s*가", text):
            scores["departure"] += 3

        if cls.RX_ALTERNATIVE.search(text):
            scores["alternative"] += 3
        if "대체역" in text or "우회" in text:
            scores["alternative"] += 3

        if cls.RX_OUTAGES.search(text) and not stations:
            scores["outages"] += 6

        if cls.RX_ROUTE.search(text):
            scores["route"] += 3
        if len(stations) >= 2:
            scores["route"] += 1
        if parsed_time:
            scores["route"] -= 2

        if cls.RX_ELEVATOR.search(text):
            scores["elevator"] += 2
        if cls.RX_GREETING.search(text) and not stations:
            scores["greeting"] += 2

        if scores["outages"] > 0:
            scores["alternative"] -= 2

        best, score = max(scores.items(), key=lambda x: x[1])
        return best if score > 0 else "elevator"

    # -------------------------------------------------------------------------
    # 응답 생성
    # -------------------------------------------------------------------------
    @staticmethod
    def _resp(session_id: str, text: str, card_type: str,
              data_updated_at: Optional[str] = None, **kw) -> ChatResponse:
        """data_updated_at: 답에 쓴 도구 결과의 updated_at. 수집 시각을 모르면 None (현재 시각으로 채우지 않음)."""
        return ChatResponse(
            session_id=session_id, text=text, card_type=card_type,
            metadata=ChatResponseMetadata(engine="fallback", fallback_used=True, data_updated_at=data_updated_at),
            **kw,
        )

    @staticmethod
    def _now_from(current_time: Optional[str]) -> datetime:
        now = datetime.now().replace(second=0, microsecond=0)
        if current_time:
            h, m = parse_hhmm(current_time, "current_time")
            now = now.replace(hour=h, minute=m)
        return now

    @classmethod
    def process(
        cls,
        message: str,
        session_id: str = "default_session",
        current_time: Optional[str] = None,
        session_ctx: Optional[Dict[str, Any]] = None,
    ) -> ChatResponse:
        """사용자 메시지를 처리하여 완전한 ChatResponse를 생성합니다.

        처리 순서: 역 추출 → 시각 파싱 → 의도 분류 → 지칭어("거기" 등) 문맥 보정 → 의도별 응답 조립.

        Args:
            message: 사용자 원문 메시지.
            session_id: 응답에 그대로 실어 보낼 세션 ID.
            current_time: 기준 시각 "HH:MM". 없으면 서버 현재 시각.
            session_ctx: {"last_station_id": ...} 형태의 세션 슬롯. 읽기만 하며,
                응답 후 슬롯 갱신은 호출하는 쪽에서 해야 합니다.

        Returns:
            metadata.engine="fallback", fallback_used=True인 ChatResponse.
        """
        text = message.strip()
        ctx = session_ctx or {}
        now = cls._now_from(current_time)

        mentions = cls.extract_mentions(text)
        stations = mentions["stations"]
        parsed_time = cls.parse_korean_time(text)
        intent = cls.classify_intent(text, stations, parsed_time)

        # 서비스 범위 밖 역을 물으면 다른 역으로 대신 답하지 않습니다.
        if mentions["unsupported"]:
            name = mentions["unsupported"][0]
            return cls._resp(
                session_id,
                f"죄송해요. '{name}'은(는) 아직 지원하지 않는 지역/노선이에요. "
                "현재는 수도권 지하철 1~8호선 역만 안내할 수 있어요.",
                "out_of_coverage",
                tool_calls=[ToolCallRecord(name="resolve_station", args={"query": name})],
                suggestions=_SUGGEST_START,
            )

        # 문맥 보정: 역 언급이 없고 "거기" 같은 지칭어가 있으면 직전 역 사용
        src = get_data_source()
        last_id = ctx.get("last_station_id")
        if not stations and cls.RX_REFERENCE.search(text) and last_id and src.station(last_id):
            sid = last_id
            st = src.station(sid)
            stations = [{"id": sid, "name": st["name"], "lines": st["lines"]}]

        if intent == "greeting" and not stations:
            return cls._resp(
                session_id,
                "안녕하세요! 지하철 엘리베이터 가동 현황과 휠체어 대체 우회 경로, 그리고 출발 시각을 안내하는 '우왕이'입니다.\n"
                "어떤 역을 도와드릴까요?",
                "help",
                suggestions=_SUGGEST_START,
            )

        if intent == "outages":
            return cls._outages(session_id, text)

        if not stations:
            if mentions["unknown"]:
                ask = f"'{mentions['unknown'][0]}역'은 찾을 수 없는 역이에요. 역 이름을 다시 확인해 주세요."
            elif intent in ("departure", "route"):
                ask = "어느 역까지 가시는지 알려 주세요. 예: \"잠실까지 3시까지 가려면?\""
            else:
                ask = "어느 역을 확인해 드릴까요? 예: \"강남역 엘리베이터 돼?\""
            return cls._resp(session_id, ask, "need_station", suggestions=_SUGGEST_START)

        if intent in ("departure", "route"):
            return cls._trip(session_id, stations, parsed_time, ctx, now, text)

        return cls._elevator(session_id, stations[0])

    @classmethod
    def _outages(cls, session_id: str, text: str = "") -> ChatResponse:
        m = cls.RX_LINE.search(text)
        line_id = m.group(1) if m else None
        res = get_broken_elevators(line_id)
        tool_calls = [ToolCallRecord(name="get_broken_elevators", args={"line_id": line_id} if line_id else {})]
        scope = f"{line_id}호선" if line_id else "수도권 지하철 1~8호선"
        limit_lines = format_limitations(res.limitations)
        if not res.outages:
            return cls._resp(
                session_id,
                "\n".join([f"현재 {scope}에서 엘리베이터 이용 불가가 확인된 역은 없습니다."] + limit_lines),
                "outages_empty", res.updated_at, tool_calls=tool_calls,
                suggestions=["강남역 엘리베이터 돼?", "사용법 알려줘"],
            )
        top = res.outages[0]
        names = ", ".join(f"{o.station_name}역" for o in res.outages[:3])
        more = " 등" if res.count > 3 else ""
        return cls._resp(
            session_id,
            "\n".join([f"현재 {line_id + '호선에서 ' if line_id else ''}엘리베이터 이용이 어려운 것으로 확인된 역은 "
                       f"총 {res.count}곳입니다: {names}{more}."] + limit_lines),
            "outages_list", res.updated_at,
            tool_calls=tool_calls,
            actions=[
                ActionPayload(type="openStation", label=f"{o.station_name}역 상세 보기",
                              payload={"station_id": o.station_id})
                for o in res.outages[:3]
            ],
            suggestions=[f"{top.station_name}역 대체역은?", "강남역 엘리베이터 돼?"],
        )

    @classmethod
    def _trip(cls, session_id: str, stations: List[dict], parsed_time: Optional[str],
              ctx: Dict[str, Any], now: datetime, text: str = "") -> ChatResponse:
        notes = []
        tool_calls = [ToolCallRecord(name="resolve_station", args={"query": s["name"]}) for s in stations[:2]]
        if len(stations) >= 2:
            origin_id, dest_id = stations[0]["id"], stations[1]["id"]
        else:
            last_id = ctx.get("last_station_id")
            last_id = last_id if last_id and get_data_source().station(last_id) else None
            if last_id and cls.RX_REFERENCE_AS_DEST.search(text):
                # "신촌에서 그 역까지": 말한 역이 출발, 지칭한 최근 역이 도착
                origin_id, dest_id = stations[0]["id"], last_id
            else:
                dest_id = stations[0]["id"]
                origin_id = last_id
                if not origin_id:
                    # 기본 출발역은 이름으로 둔다 (데이터 소스마다 역 ID 체계가 다름)
                    origin_id = resolve_station(cls.DEFAULT_ORIGIN).station_id
                    notes.append(f"출발역이 지정되지 않아 {station_name(origin_id)}역 기준으로 안내합니다.")

        origin_name, dest_name = station_name(origin_id), station_name(dest_id)
        if origin_id == dest_id:
            return cls._resp(
                session_id,
                f"출발역과 도착역이 같은 {origin_name}역이에요. 출발역과 도착역을 함께 말씀해 주세요. "
                f"예: \"신도림에서 {dest_name}까지 3시까지 가려면?\"",
                "need_station", tool_calls=tool_calls, suggestions=_SUGGEST_START,
            )

        plan_args = {"start_id": origin_id, "end_id": dest_id, "arrive_by": parsed_time}
        plan = plan_trip(**plan_args, now=now)
        tool_calls.append(ToolCallRecord(name="plan_trip", args=plan_args))

        lines = notes[:]
        if not plan.is_possible:
            lines.append(plan.warnings[0])
            return cls._resp(session_id, " ".join(lines), "trip_unsupported", tool_calls=tool_calls,
                             suggestions=[f"{dest_name}역 엘리베이터 상태는?", "지금 고장난 역 알려줘"])

        if parsed_time:
            lines.append(
                f"{origin_name}역에서 {dest_name}역까지 {parsed_time}까지 도착하시려면 "
                f"**{plan.departure_time}**에 출발하세요. ({plan.board_time} 열차 탑승, {plan.arrival_time} 도착 예정)"
            )
            if plan.is_late:
                lines.append("⏰ 지금 바로 출발해도 도착 희망 시각보다 늦을 수 있어요.")
        else:
            lines.append(
                f"{origin_name}역에서 {dest_name}역으로 {plan.departure_time}에 출발하시면 "
                f"{plan.arrival_time}경 도착합니다. (열차 {plan.route.total_stops}개 역, 엘리베이터·대기 여유 포함)"
            )
        lines += [f"⚠️ {w}" for w in plan.warnings]
        lines += format_limitations(plan.limitations)

        actions = [ActionPayload(
            type="openTime", label="출발 계산 탭에서 열기",
            payload={"originId": origin_id, "destId": dest_id, "arriveBy": parsed_time},
        )]
        for d in plan.detours:
            actions.append(ActionPayload(
                type="showAlternative", label=f"{d.alternative_station_name}역 우회 경로 보기",
                payload={"from": d.broken_station_id, "to": d.alternative_station_id,
                         "path": [d.broken_station_id, d.alternative_station_id]},
            ))

        return cls._resp(
            session_id, "\n".join(lines), "trip_plan", actions=actions, tool_calls=tool_calls,
            suggestions=[f"{dest_name}역 엘리베이터 상태는?", "지금 고장난 역 알려줘"],
        )

    @classmethod
    def _elevator(cls, session_id: str, target: dict) -> ChatResponse:
        sid = target["id"]
        status = get_elevator_status(sid)
        name = status.station_name
        tool_calls = [
            ToolCallRecord(name="resolve_station", args={"query": target["name"]}),
            ToolCallRecord(name="get_elevator_status", args={"station_id": sid}),
        ]
        actions = [ActionPayload(type="openStation", label=f"{name}역 상세 보기", payload={"station_id": sid})]

        text = describe_status(status.model_dump())
        limitations = list(status.limitations)
        if status.overall_status in ("PARTIAL", "UNAVAILABLE"):
            plan_args = {"start_id": sid, "end_id": sid}
            plan = plan_trip(**plan_args)
            tool_calls.append(ToolCallRecord(name="plan_trip", args=plan_args))
            limitations += [x for x in plan.limitations if x not in limitations]
            if plan.detours:
                d = plan.detours[0]
                text += f"\n💡 **추천 대체역**: {detour_text(d.model_dump())}"
                actions.insert(0, ActionPayload(
                    type="showAlternative", label="지도에서 우회 경로 보기",
                    payload={"from": sid, "to": d.alternative_station_id, "path": [sid, d.alternative_station_id]},
                ))
        text = "\n".join([text] + format_limitations(limitations))

        return cls._resp(
            session_id, text, "elevator_status", status.updated_at, actions=actions, tool_calls=tool_calls,
            suggestions=[f"{name}역까지 가려면 언제 출발해?", "지금 고장난 역 알려줘"],
        )
