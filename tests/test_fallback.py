"""Fallback Engine 단위 테스트.

테스트 대상: FallbackEngine (한국어 시간 파싱, 인텐트 분류, 도구 연동 응답 생성)
"""

import pytest
from AI.src.fallback_engine import FallbackEngine


def test_fallback_time_parsing():
    # 1. 자연어 시각 변환
    assert FallbackEngine.parse_korean_time("3시 반") == "15:30"
    assert FallbackEngine.parse_korean_time("오후 4시 20분") == "16:20"
    assert FallbackEngine.parse_korean_time("14:45") == "14:45"
    assert FallbackEngine.parse_korean_time("오전 9시") == "09:00"
    assert FallbackEngine.parse_korean_time("정오") == "12:00"


def test_fallback_process_elevator():
    # "강남역 엘리베이터 돼?" 질의
    resp = FallbackEngine.process("강남역 엘리베이터 지금 돼?")
    assert resp.metadata.engine == "fallback"
    assert resp.metadata.fallback_used is True
    assert "강남" in resp.text
    assert "정상" in resp.text
    assert len(resp.actions) > 0
    assert any(tc.name == "get_elevator_status" for tc in resp.tool_calls)


def test_fallback_process_alternative():
    # 고장난 역 대체역 질의
    resp = FallbackEngine.process("사당역 엘리베이터 고장났다는데 어디로 가야 해?")
    assert "대체역" in resp.text
    assert any(a.type == "showAlternative" for a in resp.actions)


def test_fallback_process_departure_reverse():
    # 출발 시각 역산 질의
    resp = FallbackEngine.process("신도림에서 강남까지 3시 반까지 가려면 언제 출발해?")
    assert "출발" in resp.text
    assert any(tc.name == "plan_trip" for tc in resp.tool_calls)
    assert any(a.type == "openTime" for a in resp.actions)


def test_fallback_process_outages():
    # 고장 목록 질의
    resp = FallbackEngine.process("지금 고장난 역 알려줘")
    assert any(tc.name == "get_broken_elevators" for tc in resp.tool_calls)
    assert "총" in resp.text or "정상" in resp.text

