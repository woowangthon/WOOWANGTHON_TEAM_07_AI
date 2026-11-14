"""pytest 공통 설정.

코드가 ``AI.src.*`` 절대 경로로 import하므로, AI/ 의 부모 폴더를 import 경로에 추가합니다.
덕분에 AI/ 안에서 ``pytest``를 실행해도, 부모 폴더에서 ``pytest AI/tests``를 실행해도 동작합니다.
"""

import sys
from pathlib import Path

_PARENT = str(Path(__file__).resolve().parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_llm_options(monkeypatch):
    """테스트가 운영 기본값(LLM_PRERESOLVE, LLM_ANSWER_MODE)에 의존하지 않게 고정합니다.

    오케스트레이터 테스트는 FakeLLM 스크립트 순서가 호출 방식(LLM이 답변 문장을 씀)에 맞춰져 있습니다.
    기본값 자체는 tests/api/test_config_defaults.py가 Settings()로 따로 확인합니다.
    """
    from AI.src.config import settings
    monkeypatch.setattr(settings, "LLM_PRERESOLVE", False)
    monkeypatch.setattr(settings, "LLM_ANSWER_MODE", "llm")
