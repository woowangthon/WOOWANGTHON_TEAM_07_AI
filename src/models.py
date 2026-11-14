"""AI 파트 데이터 모델 및 도구(Function Calling) 입출력 스키마.

명세: AI/docs/AI_DEVELOPMENT_SPEC.md §3(도구), §4(/api/chat).
*Input 모델은 LLM에 노출되는 도구 파라미터 스키마의 원본입니다 (src/tools/registry.py가 변환).
"""

from datetime import datetime
from typing import List, Optional, Literal
from pydantic import BaseModel, Field

# "HH:MM" 24시간제 (00:00 ~ 23:59)
HHMM_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


# -----------------------------------------------------------------------------
# 1. 도구(Tool) 입출력 스키마
# -----------------------------------------------------------------------------

# 1-1. resolve_station
class ResolveStationInput(BaseModel):
    query: str = Field(..., description="사용자가 언급한 지하철역 명칭 (예: '강남', '강남역', '홍대', '선릉 2호선')")


class ResolveStationOutput(BaseModel):
    station_id: Optional[str] = Field(None, description="고유 역 ID (예: 'gangnam', 'hongik')")
    name: Optional[str] = Field(None, description="표준 역명 (예: '강남', '홍대입구')")
    lines: List[str] = Field(default_factory=list, description="소속 노선 ID 목록")
    coverage: Literal["SUPPORTED", "OUT_OF_COVERAGE", "NOT_FOUND"] = Field(
        ..., description="서비스 커버리지 여부 (1~8호선 역 단위)"
    )
    confidence: float = Field(default=1.0, description="매칭 신뢰도 (1.0=정확 일치, 0.95=별칭, 0.9=미지원 키워드, 0.8=접두어 일치, 0.0=없음)")


# 1-2. get_elevator_status
# 상태 규칙은 BE 계약(BE/docs/api-contract.md §6)과 같습니다.
#   설비: BE "가동중" → AVAILABLE, "가동 안됨"(원본 보수중 포함) → UNAVAILABLE, null → UNKNOWN
#   역:   BE station_status.status null → UNKNOWN
class ElevatorSegment(BaseModel):
    segment_id: str = Field(..., description="설비 ID (BE elevator_id)")
    type: Optional[Literal["exit", "platform", "transfer"]] = Field(
        None, description="이동 축. 원본에 분류가 없으면 None (추정하지 않음)"
    )
    location: str = Field(..., description="설치 위치 원문 (예: '1번 출구', '교대 방면3-4')")
    floors: str = Field(default="", description="운행 층 구간 (예: 'B1-1F'). 없으면 빈 값")
    status: Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN"] = Field(
        ..., description="가동 상태. UNKNOWN은 상태 정보가 없거나 오래된 설비 (정상으로 간주하지 않음)"
    )
    unknown_reason: Optional[Literal["NO_DATA", "STALE"]] = Field(
        None, description="UNKNOWN 사유: NO_DATA(상태 정보 없음) / STALE(유효 시간 초과)"
    )
    fetched_at: Optional[str] = Field(None, description="상태를 수집한 시각 (ISO-8601 KST). 없으면 None")
    reason: str = Field(default="", description="고장/점검 사유. 원본에 없으면 빈 값")


class ElevatorStatusInput(BaseModel):
    station_id: str = Field(..., description="resolve_station으로 획득한 고유 역 ID")


class ElevatorStatusOutput(BaseModel):
    station_id: str
    station_name: str
    overall_status: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE", "UNKNOWN"] = Field(
        ..., description="확인된 설비만으로 판정한 역 상태. 확인된 설비가 없으면 UNKNOWN"
    )
    data_fresh: bool = Field(
        ..., description="모든 설비 상태가 유효 시간 안에 확인됐는지. False면 '정상 운행'처럼 단정하지 않음"
    )
    total_count: int = Field(default=0, description="역의 엘리베이터 수")
    unknown_count: int = Field(default=0, description="상태를 확인하지 못한 엘리베이터 수")
    updated_at: Optional[str] = Field(None, description="가장 최근 수집 시각 (ISO-8601 KST). 없으면 None")
    freshness_minutes: Optional[int] = Field(None, description="updated_at으로부터 경과된 분. 없으면 None")
    limitations: List[str] = Field(
        default_factory=list, description="사용자에게 그대로 전할 한계 안내 문장 (BE 표준 문구)"
    )
    segments: List[ElevatorSegment] = Field(default_factory=list)


# 1-3. get_broken_elevators
class BrokenElevatorsInput(BaseModel):
    line_id: Optional[str] = Field(None, description="노선 ID ('1'~'8'). 전체 조회 시 None")


class BrokenStationSummary(BaseModel):
    station_id: str
    station_name: str
    line_ids: List[str]
    broken_count: int = Field(..., description="이용 불가로 확인된 엘리베이터 수")
    total_count: int
    unknown_count: int = Field(default=0, description="상태를 확인하지 못한 엘리베이터 수")
    status: Literal["PARTIAL", "UNAVAILABLE"]
    details: List[dict] = Field(default_factory=list)


class BrokenElevatorsOutput(BaseModel):
    line_id: Optional[str] = None
    count: int = Field(..., description="이용 불가 엘리베이터가 확인된 역 수")
    updated_at: Optional[str] = None
    limitations: List[str] = Field(
        default_factory=list, description="상태를 확인하지 못한 설비가 있으면 목록이 전부가 아닐 수 있다는 안내"
    )
    outages: List[BrokenStationSummary] = Field(default_factory=list)


# 1-4. plan_trip
class PlanTripInput(BaseModel):
    start_id: str = Field(..., description="출발역 ID")
    end_id: str = Field(..., description="도착역 ID")
    arrive_by: Optional[str] = Field(
        None, pattern=HHMM_PATTERN,
        description="도착 희망 시각 (HH:MM, 24시간제). 있으면 출발 시각을 역산합니다."
    )
    depart_at: Optional[str] = Field(
        None, pattern=HHMM_PATTERN,
        description="출발 희망 시각 (HH:MM, 24시간제). arrive_by가 없을 때만 사용. 둘 다 없으면 지금 출발."
    )
    home_walk_min: Optional[int] = Field(
        7, ge=0, le=120, description="출발지에서 출발역까지 도보 소요 시간(분). 기본값 7"
    )


class RouteLeg(BaseModel):
    line_id: str
    from_id: str
    to_id: str
    stops: int
    ride_minutes: int


class RouteInfo(BaseModel):
    path: List[str]
    legs: List[RouteLeg]
    total_stops: int
    transfers: int
    total_duration_minutes: int


class DetourInfo(BaseModel):
    target_type: Literal["start", "transfer", "end"]
    broken_station_id: str
    alternative_station_id: str
    alternative_station_name: str
    surface_distance_m: Optional[int] = Field(None, description="지상 이동 거리(m). 확인된 값이 없으면 None")
    surface_walk_minutes: Optional[int] = Field(None, description="지상 도보 시간(분). 확인된 값이 없으면 None")
    stops_away: Optional[int] = Field(None, description="고장 역에서 몇 정거장 떨어졌는지 (BE 대체역 distance)")
    same_line: Optional[bool] = Field(None, description="고장 역과 노선을 하나라도 공유하는지")
    alternative_status: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE", "UNKNOWN"] = Field(
        "UNKNOWN", description="대체역의 엘리베이터 상태"
    )
    bus_tip: str = Field(default="", description="이동 안내 문구. 없으면 빈 값")
    reason: str


class TripBreakdown(BaseModel):
    home_walk: int
    station_entry_elevator: int
    worst_case_wait: int
    subway_ride: int
    transfer_walk: int
    exit_elevator: int


class PlanTripOutput(BaseModel):
    is_possible: bool = Field(..., description="False면 경로 계산을 지원하지 않는 구간이며, 시간 값은 신뢰할 수 없음 (warnings에 사유)")
    departure_time: str
    board_time: str
    arrival_time: str
    minutes_from_now: int
    is_late: bool
    route: RouteInfo
    detours: List[DetourInfo] = Field(default_factory=list)
    breakdown: TripBreakdown
    warnings: List[str] = Field(default_factory=list)
    estimated: bool = Field(True, description="시간표·실측이 아닌 추정 계산인지 (현재 항상 True)")
    limitations: List[str] = Field(
        default_factory=list, description="사용자에게 그대로 전할 한계 안내 문장 (추정 계산, 미확인 상태 등)"
    )


# -----------------------------------------------------------------------------
# 2. 클라이언트 챗봇 연동 및 세션 모델
# -----------------------------------------------------------------------------

class ActionPayload(BaseModel):
    """FE가 버튼으로 렌더링하는 액션. LLM이 아니라 백엔드가 도구 결과로 직접 만듭니다."""
    type: Literal["openStation", "showAlternative", "openTime"]
    label: str
    payload: dict


class ToolCallRecord(BaseModel):
    """응답에 함께 실어 보내는 실제 도구 호출 기록 (디버깅·FE 표시용)."""
    name: str
    args: dict


class ChatRequest(BaseModel):
    session_id: str = Field(default="default_session")
    message: str
    current_time: Optional[str] = Field(None, pattern=HHMM_PATTERN, description="기준 시각 (HH:MM). 없으면 서버 현재 시각")


class ChatResponseMetadata(BaseModel):
    engine: Literal["llm", "fallback"] = "llm"
    latency_ms: int = 0
    fallback_used: bool = False
    fallback_reason: Optional[str] = Field(
        None, description="폴백 전환 사유 (timeout | rate_limit | network | server_error | api_error | "
                          "not_configured | empty_reply | max_rounds | guard_rejected | internal_error | data_unavailable). LLM 응답이면 None"
    )
    guard_triggered: bool = Field(
        False, description="LLM 문장이 후처리 검증에 걸려 도구 결과 기반 템플릿 문장으로 바뀌었는지"
    )
    data_updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ChatResponse(BaseModel):
    session_id: str
    text: str
    card_type: Optional[str] = None
    actions: List[ActionPayload] = Field(default_factory=list)
    tool_calls: List[ToolCallRecord] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    metadata: ChatResponseMetadata = Field(default_factory=ChatResponseMetadata)

