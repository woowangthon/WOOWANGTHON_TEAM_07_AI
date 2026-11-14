"""운영 기본 설정 테스트 (팀 결정 2026-11-14, docs/BENCHMARK.md §4).

환경 변수가 없을 때의 기본값을 확인한다. conftest의 autouse 고정은 전역 settings만 바꾸므로 영향이 없다.
"""

import pytest

from AI.src.config import Settings

ENV_KEYS = ["FACTCHAT_MODEL", "LLM_PRERESOLVE", "LLM_ANSWER_MODE", "BE_ROUTE_ESTIMATE"]


@pytest.fixture
def clean_env(monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_default_model_and_speed_options_are_the_measured_fast_combo(clean_env):
    s = Settings()
    assert s.FACTCHAT_MODEL == "gemini-3.5-flash-lite"
    assert s.LLM_PRERESOLVE is True
    assert s.LLM_ANSWER_MODE == "template"
    assert s.LLM_TIMEOUT_SECONDS == 3.0


def test_route_estimate_is_off_by_default_and_env_can_enable_it(clean_env, monkeypatch):
    assert Settings().BE_ROUTE_ESTIMATE is False
    monkeypatch.setenv("BE_ROUTE_ESTIMATE", "true")
    assert Settings().BE_ROUTE_ESTIMATE is True


def test_env_can_still_restore_previous_defaults(clean_env, monkeypatch):
    monkeypatch.setenv("FACTCHAT_MODEL", "gpt-5.1")
    monkeypatch.setenv("LLM_PRERESOLVE", "false")
    monkeypatch.setenv("LLM_ANSWER_MODE", "llm")
    s = Settings()
    assert (s.FACTCHAT_MODEL, s.LLM_PRERESOLVE, s.LLM_ANSWER_MODE) == ("gpt-5.1", False, "llm")
