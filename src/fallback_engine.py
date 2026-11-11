"""규칙 기반 폴백 엔진 (Fallback Engine).

FE/Prototype-v2/js/ai-agent.js의 규칙 기반 의도 분류 및 도구 호출 시뮬레이션 로직을 Python으로 100% 포팅한 모듈입니다.
LLM API 장애, 타임아웃(2.5초 초과), Rate Limit(429) 발생 시 즉시 무중단 대체 응답을 생성합니다.
"""

from datetime import datetime
import re
from typing import Optional, List, Dict, Any
from .models import (
    ChatResponse, ChatResponseMetadata, ActionPayload, ToolCallRecord
)
from .tools import (
    resolve_station, get_elevator_status, get_broken_elevators, plan_trip
)
from .mock_data import STATIONS, ALIASES, ELEVATORS


class FallbackEngine:
    """LLM 없이 정규식 규칙만으로 ChatResponse를 만드는 폴백 엔진.

    모든 메서드는 classmethod이므로 인스턴스 없이 ``FallbackEngine.process(...)``로 호출합니다.
    의도 분류 점수표와 시각 파싱 규칙은 AI/docs/IMPLEMENTATION_STATUS.md §4 참고.
    """

    # 출발역을 알 수 없을 때 쓰는 기본 출발역 (시스템 프롬프트 명세와 동일하게 신도림)
    DEFAULT_ORIGIN = "sindorim"

    # 정규식 패턴 정의 (Prototype-v2의 RX 그대로 포팅)
    RX_GREETING = re.compile(r"안녕|도움|뭐\s*할\s*수|사용법|도와줘|하이|hello|\bhi\b|help", re.IGNORECASE)
    RX_OUTAGES = re.compile(r"고장\s*난\s*(역|곳|데)|점검\s*중인\s*(역|곳)|어디\s*(가|가요)?\s*고장|현황|전체|목록|리스트")
    RX_ALTERNATIVE = re.compile(r"고장|안\s*돼|안\s*되|못\s*타|못\s*써|대체|우회|다른\s*역|어디로|멈췄|멈춰")
    RX_ELEVATOR = re.compile(r"엘리베이터|엘베|승강기|돼\s*\?|되나|돼요|되요|운행|상태|휠체어|유모차|이용\s*가능|탈\s*수")
    RX_DEPARTURE_STRONG = re.compile(r"출발|몇\s*시에|몇\s*시쯤|언제\s*나가|늦지\s*않게|나가야|나서야|집에서\s*언제")
    RX_DEPARTURE_WEAK = re.compile(r"까지\s*가려면|도착하려면|까지\s*도착|에\s*도착|까지\s*가야")
    RX_ROUTE = re.compile(r"가는\s*길|경로|어떻게\s*가|환승|길\s*(좀\s*)?알려|가는\s*방법|가려면")
    RX_REFERENCE = re.compile(r"거기|그\s*역|그곳|그\s*쪽|그럼|그러면|그\s*데|여기")

    KOREAN_NUMS = {
        "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6,
        "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "열한": 11, "열두": 12
    }

    @classmethod
    def parse_korean_time(cls, text: str) -> Optional[str]:
        """한국어 자연어 시각 표현을 HH:MM (24시간제) 형식으로 파싱합니다."""
        t = text.strip()
        
        # 1. HH:MM 형식
        m = re.search(r"(\d{1,2})\s*:\s*(\d{2})", t)
        if m:
            h, minute = int(m.group(1)), int(m.group(2))
            return f"{h:02d}:{minute:02d}"

        # 2. 정오 / 자정
        if "정오" in t:
            return "12:00"
        if "자정" in t:
            return "00:00"

        # 3. '오후/오전 N시 M분/반'
        is_pm = bool(re.search(r"오후|저녁|밤", t))
        is_am = bool(re.search(r"오전|아침|새벽", t))

        time_match = re.search(r"(\d{1,2}|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|열한|열두)\s*시(?:\s*(\d{1,2})분|\s*(반))?", t)
        if not time_match:
            return None

        h_str = time_match.group(1)
        h = cls.KOREAN_NUMS.get(h_str, int(h_str) if h_str.isdigit() else 0)

        m_str = time_match.group(2)
        half_str = time_match.group(3)

        minute = 30 if half_str else (int(m_str) if m_str else 0)

        if is_pm and h < 12:
            h += 12
        elif is_am and h == 12:
            h = 0
        elif not is_am and not is_pm and 1 <= h <= 6:
            # 관례상 1~6시는 오후로 추정
            h += 12

        return f"{h:02d}:{minute:02d}"

    @classmethod
    def extract_stations(cls, text: str) -> List[Dict[str, Any]]:
        """텍스트에 언급된 지하철역들을 순서대로 추출합니다."""
        results = []
        seen = set()

        # 알려진 별칭 및 역명 검색
        words = re.findall(r"[가-힣0-9a-zA-Z_]+", text)
        for w in words:
            res = resolve_station(w)
            if res.coverage == "SUPPORTED" and res.station_id and res.station_id not in seen:
                seen.add(res.station_id)
                results.append({"id": res.station_id, "name": res.name, "lines": res.lines})

        return results

    @classmethod
    def classify_intent(cls, text: str, stations: List[Dict[str, Any]], parsed_time: Optional[str]) -> str:
        """점수 기반 규칙으로 사용자 의도를 분류합니다."""
        scores = {
            "departure": 0, "alternative": 0, "outages": 0,
            "route": 0, "elevator": 0, "greeting": 0
        }

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

        if cls.RX_OUTAGES.search(text):
            scores["outages"] += 0 if stations else 6

        if cls.RX_ROUTE.search(text):
            scores["route"] += 3
        if len(stations) >= 2:
            scores["route"] += 1
        if parsed_time:
            scores["route"] -= 2

        if cls.RX_ELEVATOR.search(text):
            scores["elevator"] += 2
        if cls.RX_GREETING.search(text):
            scores["greeting"] += 0 if stations else 2

        if scores["outages"] > 0:
            scores["alternative"] -= 2

        best = max(scores.items(), key=lambda x: x[1])
        return best[0] if best[1] > 0 else "elevator"

    @classmethod
    def process(
        cls,
        message: str,
        session_id: str = "default_session",
        current_time: Optional[str] = None,
        session_ctx: Optional[Dict[str, Any]] = None
    ) -> ChatResponse:
        """사용자 메시지를 처리하여 완전한 ChatResponse를 생성합니다.

        처리 순서: 역 추출 → 시각 파싱 → 의도 분류 → 지칭어("거기" 등) 문맥 보정 → 의도별 응답 조립.

        Args:
            message: 사용자 원문 메시지.
            session_id: 응답에 그대로 실어 보낼 세션 ID.
            current_time: 현재 사용하지 않습니다. (시간 계산은 서버의 현재 시각 기준)
            session_ctx: {"last_station_id": ...} 형태의 세션 슬롯. 읽기만 하며,
                응답 후 슬롯 갱신은 호출하는 쪽에서 해야 합니다.

        Returns:
            metadata.engine="fallback", fallback_used=True인 ChatResponse.
        """
        text = message.strip()
        ctx = session_ctx or {}

        stations = cls.extract_stations(text)
        parsed_time = cls.parse_korean_time(text)
        intent = cls.classify_intent(text, stations, parsed_time)

        # 문맥 보정: 역명이 없고 "거기" 등의 지칭어가 있는 경우 직전 역 사용
        if not stations and cls.RX_REFERENCE.search(text) and ctx.get("last_station_id"):
            last_id = ctx["last_station_id"]
            st = STATIONS.get(last_id)
            if st:
                stations = [{"id": last_id, "name": st["name"], "lines": st["lines"]}]

        # ---------------------------------------------------------------------
        # 1. 인사 / 도움말
        # ---------------------------------------------------------------------
        if intent == "greeting" and not stations:
            return ChatResponse(
                session_id=session_id,
                text="안녕하세요! 지하철 엘리베이터 가동 현황과 휠체어 대체 우회 경로, 그리고 출발 시각을 안내하는 '우왕이'입니다.\n어떤 역을 도와드릴까요?",
                card_type="help",
                suggestions=["강남역 엘리베이터 돼?", "신도림에서 잠실까지 3시까지 가려면?", "지금 고장난 역 알려줘"],
                metadata=ChatResponseMetadata(engine="fallback", fallback_used=True)
            )

        # ---------------------------------------------------------------------
        # 2. 고장 목록 조회 (outages)
        # ---------------------------------------------------------------------
        if intent == "outages":
            outages_res = get_broken_elevators()
            tool_calls = [ToolCallRecord(name="get_broken_elevators", args={})]
            actions: List[ActionPayload] = []

            if not outages_res.outages:
                return ChatResponse(
                    session_id=session_id,
                    text="현재 수도권 지하철 1~8호선 모든 역의 엘리베이터가 정상 운행 중입니다 🙂",
                    card_type="outages_empty",
                    tool_calls=tool_calls,
                    suggestions=["강남역 엘리베이터 돼?", "사용법 알려줘"],
                    metadata=ChatResponseMetadata(engine="fallback", fallback_used=True)
                )

            top_outage = outages_res.outages[0]
            actions.append(ActionPayload(
                type="openStation",
                label=f"{top_outage.station_name}역 상세 보기",
                payload={"station_id": top_outage.station_id}
            ))

            summary_text = f"현재 엘리베이터 이용 불가 역은 총 {outages_res.count}곳입니다.\n"
            summary_text += f"가장 심각한 곳은 {top_outage.station_name}역({top_outage.status})입니다."

            return ChatResponse(
                session_id=session_id,
                text=summary_text,
                card_type="outages_list",
                actions=actions,
                tool_calls=tool_calls,
                suggestions=[f"{top_outage.station_name}역 대체역은?", "강남역 엘리베이터 돼?"],
                metadata=ChatResponseMetadata(engine="fallback", fallback_used=True)
            )

        # ---------------------------------------------------------------------
        # 3. 출발 시각 역산 / 경로 탐색 (departure / route)
        # ---------------------------------------------------------------------
        if intent in ["departure", "route"]:
            origin_id = cls.DEFAULT_ORIGIN
            dest_id = cls.DEFAULT_ORIGIN

            if len(stations) >= 2:
                # "A에서 B까지" 판정
                origin_id = stations[0]["id"]
                dest_id = stations[1]["id"]
            elif len(stations) == 1:
                dest_id = stations[0]["id"]
                origin_id = ctx.get("last_station_id") or cls.DEFAULT_ORIGIN

            plan_res = plan_trip(
                start_id=origin_id,
                end_id=dest_id,
                arrive_by=parsed_time
            )

            tool_calls = [
                ToolCallRecord(name="resolve_station", args={"query": dest_id}),
                ToolCallRecord(name="plan_trip", args={"start_id": origin_id, "end_id": dest_id, "arrive_by": parsed_time})
            ]

            actions = [
                ActionPayload(
                    type="openTime",
                    label="출발 계산 탭에서 열기",
                    payload={"originId": origin_id, "destId": dest_id, "arriveBy": parsed_time}
                )
            ]

            dest_name = STATIONS.get(dest_id, {}).get("name", dest_id)
            origin_name = STATIONS.get(origin_id, {}).get("name", origin_id)

            if parsed_time:
                msg = f"{origin_name}역에서 {dest_name}역까지 {parsed_time}까지 도착하시려면 **{plan_res.departure_time}**에 출발하세요. "
                msg += f"({plan_res.board_time} 열차 탑승 시 {plan_res.arrival_time} 도착 예정)"
            else:
                msg = f"{origin_name}역에서 {dest_name}역으로 지금 출발하시면 {plan_res.departure_time}에 나서서 {plan_res.arrival_time}경 도착합니다."

            if plan_res.warnings:
                msg += f"\n⚠️ {plan_res.warnings[0]}"

            return ChatResponse(
                session_id=session_id,
                text=msg,
                card_type="trip_plan",
                actions=actions,
                tool_calls=tool_calls,
                suggestions=[f"{dest_name}역 엘리베이터 상태는?", "지금 고장난 역 알려줘"],
                metadata=ChatResponseMetadata(engine="fallback", fallback_used=True)
            )

        # ---------------------------------------------------------------------
        # 4. 엘리베이터 상태 / 대체역 안내 (elevator / alternative)
        # ---------------------------------------------------------------------
        target_st = stations[0] if stations else {"id": "gangnam", "name": "강남"}
        sid = target_st["id"]
        status_res = get_elevator_status(sid)

        tool_calls = [
            ToolCallRecord(name="resolve_station", args={"query": target_st["name"]}),
            ToolCallRecord(name="get_elevator_status", args={"station_id": sid})
        ]

        actions = [
            ActionPayload(
                type="openStation",
                label=f"{status_res.station_name}역 상세 보기",
                payload={"station_id": sid}
            )
        ]

        if status_res.overall_status == "AVAILABLE":
            resp_text = f"현재 {status_res.station_name}역의 모든 엘리베이터는 정상 운행 중입니다. 휠체어 이용에 불편함이 없습니다 🙂"
        elif status_res.overall_status in ["PARTIAL", "UNAVAILABLE"]:
            resp_text = f"현재 {status_res.station_name}역의 엘리베이터는 **{status_res.overall_status}** 상태로 일부 또는 전체 구간 이용이 어렵습니다."
            plan_res = plan_trip(start_id=sid, end_id=sid)
            if plan_res.detours:
                detour = plan_res.detours[0]
                resp_text += f"\n💡 **추천 대체역**: 인접한 **{detour.alternative_station_name}역** 엘리베이터를 이용하세요 ({detour.bus_tip})."
                actions.insert(0, ActionPayload(
                    type="showAlternative",
                    label="지도에서 우회 경로 보기",
                    payload={"from": sid, "to": detour.alternative_station_id, "path": [sid, detour.alternative_station_id]}
                ))
        else:
            resp_text = f"{status_res.station_name}역의 실시간 엘리베이터 정보가 현재 준비되지 않았습니다."

        return ChatResponse(
            session_id=session_id,
            text=resp_text,
            card_type="elevator_status",
            actions=actions,
            tool_calls=tool_calls,
            suggestions=[f"{status_res.station_name}역까지 가려면 언제 출발해?", "지금 고장난 역 알려줘"],
            metadata=ChatResponseMetadata(engine="fallback", fallback_used=True)
        )

