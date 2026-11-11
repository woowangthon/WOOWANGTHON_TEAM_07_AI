"""AI 파트 데이터 모델 및 도구(Function Calling) 입출력 스키마.

AI/docs/AI_DEVELOPMENT_SPEC.md 명세를 100% 준수합니다.
"""

from datetime import datetime
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


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
    confidence: float = Field(default=1.0, description="매칭 신뢰도 (0.0~1.0)")


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
    arrive_by: Optional[str] = Field(None, description="도착 희망 시각 (HH:MM 형식)")
    depart_at: Optional[str] = Field(None, description="출발 희망 시각 (HH:MM 형식)")
    home_walk_min: Optional[int] = Field(7, description="출발지에서 역까지 도보 소요 시간(분)")


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
    is_possible: bool
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
    type: Literal["openStation", "showAlternative", "openTime"]
    label: str
    payload: dict


class ToolCallRecord(BaseModel):
    name: str
    args: dict


class ChatRequest(BaseModel):
    session_id: str = Field(default="default_session")
    message: str
    current_time: Optional[str] = None  # HH:MM


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

