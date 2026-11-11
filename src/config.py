"""AI 서비스 환경 설정 모듈.

학교 마인드로직 팩트챗(FactChat) 게이트웨이 및 모델 설정을 관리합니다.

참고:
    - 값은 import 시점의 OS 환경 변수에서 읽습니다. ``.env`` 파일은 자동으로 읽지 않습니다.
    - 아직 어떤 모듈도 이 설정을 import하지 않습니다. (``schoolApi.py``는 자체 기본값 사용)
      향후 FastAPI 오케스트레이터가 ``from AI.src.config import settings``로 사용할 예정입니다.
    - ``LLM_TIMEOUT_SECONDS`` 기본값 3.0초는 명세서의 2.5초와 다르므로 확정이 필요합니다.
"""

import os
from pydantic import BaseModel, Field


class Settings(BaseModel):
    # 학교 마인드로직 FactChat 게이트웨이 설정
    FACTCHAT_API_KEY: str = Field(
        default_factory=lambda: os.getenv("FACTCHAT_API_KEY", "")
    )
    FACTCHAT_BASE_URL: str = Field(
        default_factory=lambda: os.getenv(
            "FACTCHAT_BASE_URL", "https://factchat-cloud.mindlogic.ai/v1/gateway"
        )
    )
    # 지원 모델: "claude-sonnet-5" 또는 "gpt-4o-mini"
    FACTCHAT_MODEL: str = Field(
        default_factory=lambda: os.getenv("FACTCHAT_MODEL", "claude-sonnet-5")
    )

    # 비기능 요구사항
    LLM_TIMEOUT_SECONDS: float = Field(
        default=3.0, description="LLM 응답 타임아웃 (초). 초과 시 FallbackEngine으로 즉시 전환"
    )
    SESSION_TTL_MINUTES: int = Field(
        default=30, description="세션 슬롯 캐시 유지 시간"
    )


settings = Settings()
