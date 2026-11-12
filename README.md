# 우왕톤 7팀 AI 파트 (WOOWANGTHON TEAM 07 AI)

수도권 지하철 교통약자(휠체어·유모차 이용자)를 위한 실시간 엘리베이터 상태 기반 대체 경로 & AI 출발 안내 서비스의 AI 파트 레포지토리입니다.

> AI 코딩 에이전트는 [`AGENTS.md`](./AGENTS.md)를 먼저 읽으세요.

---

## 1. 문서

| 문서 | 내용 |
|---|---|
| [`AGENTS.md`](./AGENTS.md) | AI 에이전트용 작업 가이드 — 원칙, 명령어, 코드 지도, 작업 규칙 |
| [`docs/IMPLEMENTATION_STATUS.md`](./docs/IMPLEMENTATION_STATUS.md) | 현재 구현 현황 — 도구·폴백 엔진의 실제 동작, 명세와의 차이, 변경 이력 |
| [`docs/AI_DEVELOPMENT_SPEC.md`](./docs/AI_DEVELOPMENT_SPEC.md) | 기술 명세 (목표 설계) — 아키텍처, 도구 스키마, `/api/chat`, 프롬프트, R&R, 평가 계획 |
| [`docs/DESIGN_REVIEW.md`](./docs/DESIGN_REVIEW.md) | 초기 설계 검토 및 수정안 (기록용) |
| [`../docs/cards/004-ai-development-plan.md`](../docs/cards/004-ai-development-plan.md) | 작업 카드 [CARD-004] |
| [`../docs/decisions/001-ai-architecture-and-tool-design.md`](../docs/decisions/001-ai-architecture-and-tool-design.md) | 아키텍처 결정 기록 [DECISION-001] |

---

## 2. 핵심 원칙 및 아키텍처

**LLM은 해석하고, 백엔드는 계산한다.** LLM은 의도 파악과 안내 문장만 맡고, 경로·시간·엘리베이터 상태는 도구가 계산하며, 화면용 액션 데이터는 백엔드가 직접 조립합니다.

```text
[ 사용자 질의 ]
       ↓
[ AI 오케스트레이터 (FastAPI) ]                       ← 미구현 (Phase 2)
  ├── 1. 세션 슬롯 주입 + LLM 도구 호출 (의도 분석, 슬롯 추출)   src/llm/, src/tools/registry.py
  ├── 2. 도구 실행 (역명 정규화, 엘리베이터 상태, 경로/시간 계산)  src/tools/
  ├── 3. 액션 데이터 백엔드 직접 조립 (UI 렌더링용)
  ├── 4. LLM이 도구 결과로 안내 문장 생성
  ├── 5. 후처리 검증 (환각 감지 시 템플릿 문장으로 대체)          ← 미구현 (Phase 3)
  └── [장애/타임아웃 시] 규칙 기반 폴백 엔진                      src/fallback/
       ↓
[ 프론트엔드 모바일 웹 챗봇 렌더링 ]
```

---

## 3. 폴더 구조

```text
AI/
├── AGENTS.md / CLAUDE.md / GEMINI.md   # 에이전트 가이드 (CLAUDE·GEMINI는 AGENTS를 불러옴)
├── src/                     # AI.src 패키지
│   ├── config.py            # 게이트웨이·타임아웃 설정, AI/.env 자동 로딩
│   ├── models.py            # Pydantic 스키마: 도구 입출력 + /api/chat 요청/응답 (FE/BE 계약)
│   ├── data/
│   │   └── mock_data.py     # 역·별칭·엘리베이터·대체역·시간 상수 Mock 데이터
│   ├── tools/               # Function Calling 도구
│   │   ├── station.py       # resolve_station — 역 이름 정규화, 서비스 범위 판정
│   │   ├── elevator.py      # get_elevator_status, get_broken_elevators
│   │   ├── trip.py          # plan_trip — 경로 + 고장 우회 + 출발 시각 역산
│   │   ├── registry.py      # LLM용 도구 정의(TOOL_SPECS) 자동 생성 + 실행기(execute_tool)
│   │   └── common.py        # 시각 파싱 등 공용 유틸
│   ├── llm/
│   │   └── prompts.py       # 시스템 프롬프트 템플릿 (오케스트레이터도 이 패키지에 추가 예정)
│   └── fallback/
│       └── engine.py        # LLM 장애 시 규칙 기반 응답 엔진
├── scripts/
│   └── llm_demo.py          # LLM 게이트웨이 도구 호출 왕복 데모 (FactChat / NVIDIA)
├── tests/                   # pytest 단위 테스트 (src/와 같은 구조: data/, tools/, fallback/)
├── docs/                    # 명세, 구현 현황, 설계 검토
├── .env.example             # 환경 변수 템플릿
├── requirements.txt
└── conftest.py / pytest.ini
```

---

## 4. 빠른 시작

Python 3.12 기준으로 검증했습니다.

```powershell
cd AI
pip install -r requirements.txt

# 테스트
python -m pytest

# LLM 연동 데모: .env.example을 .env로 복사해 API 키를 넣은 뒤
python scripts/llm_demo.py "신도림에서 강남까지 3시까지 가려면?"
python scripts/llm_demo.py --provider nvidia "사당역 엘리베이터 돼?"
```

- `.env`는 `src/config.py`가 자동으로 읽습니다. 셸에 같은 환경 변수가 있으면 셸 값이 우선합니다.
- 기본 모델은 FactChat `gpt-5.1`입니다. `--model` 옵션이나 `FACTCHAT_MODEL`로 바꿀 수 있습니다.
- `AI/`의 부모 폴더에서 실행해도 됩니다: `python -m pytest AI/tests`, `python AI/scripts/llm_demo.py`.

---

## 5. 진행 상황

| 단계 | 내용 | 상태 |
|---|---|---|
| **Phase 1** | 도구 4종 스키마/Mock 구현, 규칙 기반 폴백 엔진 포팅 | ✅ 완료 |
| **Phase 2** | FastAPI 오케스트레이터 및 Function Calling 연동 | 🔄 도구 정의·프롬프트·게이트웨이 데모 완료, `/api/chat`·세션 메모리 미구현 |
| **Phase 3** | 후처리 환각 가드 및 백엔드 도메인 서비스 연동 | ⏳ |
| **Phase 4** | 50개 벤치마크 평가, 프롬프트 튜닝, FE E2E 통합 테스트 | ⏳ |

현재는 Mock 데이터로 2호선 순환선 구간만 경로를 계산합니다. 자세한 제약은 [`docs/IMPLEMENTATION_STATUS.md`](./docs/IMPLEMENTATION_STATUS.md)를 참고하세요.
