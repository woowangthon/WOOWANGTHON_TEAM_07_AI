"""LLM 연동 계층.

    prompts.py           시스템 프롬프트 템플릿
    client.py            LLM 클라이언트 (OpenAI 호환 게이트웨이), LLMUnavailableError
    orchestrator.py      /api/chat 처리: 도구 호출 루프, 폴백 전환, 후처리 검증, 세션 갱신
    response_builder.py  도구 결과 → card_type·actions·suggestions·템플릿 문장
    guard.py             후처리 환각 검증 (역명·시각)
    session.py           세션 슬롯 메모리 (TTL)
"""
