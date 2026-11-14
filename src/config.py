"""AI 서비스 환경 설정 모듈.

LLM 게이트웨이(학교 FactChat, NVIDIA) 접속 정보와 비기능 요구사항 값을 관리합니다.

값을 읽는 순서: OS 환경 변수 → AI/.env 파일 → 아래 기본값.
(.env는 이미 설정된 환경 변수를 덮어쓰지 않습니다. 별도 패키지 없이 KEY=VALUE 줄만 읽습니다.)
"""

import os
from pathlib import Path
from typing import Dict

from pydantic import BaseModel, Field

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env_file(path: Path = ENV_FILE) -> None:
    """KEY=VALUE 형식의 .env 파일을 읽어 아직 없는 환경 변수만 채웁니다."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value.startswith("your_"):  # .env.example의 자리표시자를 그대로 둔 경우
            continue
        os.environ.setdefault(key.strip(), value)


load_env_file()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


class Settings(BaseModel):
    # 학교 마인드로직 FactChat 게이트웨이 (OpenAI 호환 API)
    FACTCHAT_API_KEY: str = Field(default_factory=lambda: _env("FACTCHAT_API_KEY"))
    FACTCHAT_BASE_URL: str = Field(
        default_factory=lambda: _env("FACTCHAT_BASE_URL", "https://factchat-cloud.mindlogic.ai/v1/gateway")
    )
    FACTCHAT_MODEL: str = Field(default_factory=lambda: _env("FACTCHAT_MODEL", "gpt-5.1"))

    # NVIDIA API 게이트웨이 (OpenAI 호환 API, 비교 실험용)
    NVIDIA_API_KEY: str = Field(default_factory=lambda: _env("NVIDIA_API_KEY"))
    NVIDIA_BASE_URL: str = Field(default_factory=lambda: _env("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"))
    NVIDIA_MODEL: str = Field(default_factory=lambda: _env("NVIDIA_MODEL", "z-ai/glm-5.3-flash"))

    # 비기능 요구사항
    # ※ 명세(AI_DEVELOPMENT_SPEC.md)는 2.5초. 오케스트레이터 구현 시 팀에서 확정할 것.
    LLM_TIMEOUT_SECONDS: float = Field(
        default=3.0, description="LLM 응답 타임아웃 (초). 초과 시 FallbackEngine으로 즉시 전환"
    )
    SESSION_TTL_MINUTES: int = Field(default=30, description="세션 슬롯 캐시 유지 시간")

    # /api/chat 오케스트레이터
    LLM_PROVIDER: str = Field(
        default_factory=lambda: _env("LLM_PROVIDER", "factchat"),
        description="오케스트레이터가 쓸 게이트웨이 (factchat | nvidia)",
    )
    LLM_MAX_TOOL_ROUNDS: int = Field(
        default=4, description="한 요청에서 LLM↔도구 왕복 최대 횟수. 넘으면 폴백으로 전환"
    )

    # 응답 속도 옵션 (이슈 #17). 기본값은 기존 동작. 효과는 docs/BENCHMARK.md
    LLM_PRERESOLVE: bool = Field(
        default_factory=lambda: _env("LLM_PRERESOLVE", "false").strip().lower() in ("1", "true", "yes", "on"),
        description="문장 속 역을 코드가 먼저 resolve_station으로 확인해 프롬프트에 넣음 (LLM 왕복 1회 절약)",
    )
    LLM_ANSWER_MODE: str = Field(
        default_factory=lambda: _env("LLM_ANSWER_MODE", "llm").strip().lower(),
        description="llm: 도구 결과를 LLM에 돌려줘 문장 생성 / template: 데이터 도구 실행 후 템플릿 문장으로 바로 답함 (왕복 1회 절약)",
    )

    # BE API 데이터 소스 (AI_DATA_SOURCE=be일 때). 이슈 #4, #5
    BE_API_BASE_URL: str = Field(
        default_factory=lambda: _env("BE_API_BASE_URL", "http://127.0.0.1:8000"),
        description="BE 서버 주소 (BE/docs/frontend-integration.md의 Base URL)",
    )
    BE_API_TIMEOUT_SECONDS: float = Field(
        default_factory=lambda: float(_env("BE_API_TIMEOUT_SECONDS", "2.0")),
        description="BE API 호출 1회 타임아웃 (초). 넘으면 '확인할 수 없음'으로 안내",
    )

    # HTTP 서버
    CORS_ALLOW_ORIGINS: str = Field(
        default_factory=lambda: _env("CORS_ALLOW_ORIGINS", "*"),
        description="/api/chat을 부를 수 있는 FE 출처 (쉼표로 구분). 기본 * (개발용, 쿠키·인증 없음)",
    )

    def cors_origins(self) -> list:
        return [o.strip() for o in self.CORS_ALLOW_ORIGINS.split(",") if o.strip()]

    def provider(self, name: str) -> Dict[str, str]:
        """게이트웨이 이름("factchat" | "nvidia")으로 api_key / base_url / model을 반환합니다."""
        if name == "factchat":
            return {"api_key": self.FACTCHAT_API_KEY, "base_url": self.FACTCHAT_BASE_URL, "model": self.FACTCHAT_MODEL}
        if name == "nvidia":
            return {"api_key": self.NVIDIA_API_KEY, "base_url": self.NVIDIA_BASE_URL, "model": self.NVIDIA_MODEL}
        raise ValueError(f"알 수 없는 게이트웨이: {name} (factchat | nvidia)")


settings = Settings()
