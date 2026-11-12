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
class ElevatorSegment(BaseModel):
    segment_id: str = Field(..., description="구간 ID (예: 'ground_to_concourse')")
    type: Literal["exit", "platform", "transfer"] = Field(..., description="엘리베이터 이동 축/위치")
    location: str = Field(..., description="설치 위치 (예: '1번 출구 앞')")
    floors: str = Field(..., description="운행 층 (예: '지상 ↔ B1 대합실')")
    status: Literal["AVAILABLE", "UNAVAILABLE", "MAINTENANCE"] = Field(..., description="가동 상태")
    reason: str = Field(default="", description="고장/점검 사유")


class ElevatorStatusInput(BaseModel):
    station_id: str = Field(..., description="resolve_station으로 획득한 고유 역 ID")


class ElevatorStatusOutput(BaseModel):
    station_id: str
    station_name: str
    overall_status: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE", "UNKNOWN"]
    updated_at: str = Field(..., description="데이터 기준 시각 (ISO-8601)")
    freshness_minutes: int = Field(default=0, description="기준 시각으로부터 경과된 분")
    segments: List[ElevatorSegment] = Field(default_factory=list)


# 1-3. get_broken_elevators
class BrokenElevatorsInput(BaseModel):
    line_id: Optional[str] = Field(None, description="노선 ID ('1'~'8'). 전체 조회 시 None")


class BrokenStationSummary(BaseModel):
    station_id: str
    station_name: str
    line_ids: List[str]
    broken_count: int
    total_count: int
    status: Literal["PARTIAL", "UNAVAILABLE"]
    details: List[dict] = Field(default_factory=list)


class BrokenElevatorsOutput(BaseModel):
    line_id: Optional[str] = None
    count: int = Field(..., description="고장 역 수")
    updated_at: str
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
    surface_distance_m: int
    surface_walk_minutes: int
    bus_tip: str
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
    data_updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ChatResponse(BaseModel):
    session_id: str
    text: str
    card_type: Optional[str] = None
    actions: List[ActionPayload] = Field(default_factory=list)
    tool_calls: List[ToolCallRecord] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    metadata: ChatResponseMetadata = Field(default_factory=ChatResponseMetadata)

