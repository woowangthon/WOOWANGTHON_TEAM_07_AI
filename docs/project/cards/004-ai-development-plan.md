# [CARD-004] AI 파트 개발 계획 및 기술 명세 수립

- **작업 레벨**: Standard
- **상태**: 완료 (문서 수립 완료)
- **작성일**: 2026-11-11
- **관련 문서**: `AI/docs/DESIGN_REVIEW.md`, `AI/docs/AI_DEVELOPMENT_SPEC.md`, `docs/decisions/001-ai-architecture-and-tool-design.md`

---

## 1. 요구 요약 (3줄 이내)
1. `AI/docs/DESIGN_REVIEW.md`를 바탕으로 "LLM은 해석, 백엔드는 계산" 원칙을 구체화한 AI 파트 기술 명세 및 개발 로드맵을 수립한다.
2. 결함이 있던 기존 도구 명세를 4개 도구(`resolve_station`, `get_elevator_status`, `get_broken_elevators`, `plan_trip`)로 통합·재정의하고, 액션 데이터 백엔드 조립 및 환각 후처리 검증 파이프라인을 설계한다.
3. AI 파트와 백엔드 간의 R&R을 명확히 하고, 규칙 기반 폴백 엔진 및 평가 셋(Eval Benchmark)을 포함한 단계별 개발 진행 계획을 수립한다.

---

## 2. 작업 완료 조건 (DoD)
- [x] `AI/docs/DESIGN_REVIEW.md`의 지적 사항(출발역 누락, 5개→4개 도구 통합, 평균 대기시간 오류 등)을 반영한 세부 도구 스키마 명세 작성
- [x] LLM의 자연어 생성과 백엔드의 액션 데이터 조립을 분리하는 요청/응답 오케스트레이션 파이프라인 설계
- [x] AI 파트와 백엔드 간의 명확한 역할 경계(R&R) 정의
- [x] 규칙 기반 폴백(Rule-based Fallback) 및 환각 가드(Hallucination Guard) 설계
- [x] 4단계 마일스톤(Phase 1~4) 및 평가 벤치마크 계획 수립
- [x] `docs/decisions/001-ai-architecture-and-tool-design.md` 결정 기록 작성
- [x] `docs/PROJECT-STATE.md` 상태 문서 갱신

---

## 3. 핵심 설계 요약

### 3.1 변경 전 vs 변경 후 아키텍처 비교

| 항목 | 기존 프로토타입 / 초안 | 수정안 (CARD-004 반영) |
|---|---|---|
| **실행 주체** | 브라우저 인메모리 규칙 로직 (`ai-agent.js`) | FastAPI 백엔드 + LLM Function Calling 에이전트 |
| **도구 구성** | 5개 도구 (출발역 없는 `calculate_departure_time`, 중복 도구) | **4개 도구** (`resolve_station`, `get_elevator_status`, `get_broken_elevators`, `plan_trip`) |
| **액션 데이터 생성** | LLM이 카드 HTML/JSON을 직접 재작성 | **백엔드가 도구 결과로부터 직접 조립**, LLM은 자연어 설명만 생성 |
| **시간 역산 공식** | 배차 간격의 절반(평균 대기 시간) 사용 | **최악의 대기 시간(배차 간격 전체 or 안전 마진)** 반영 (지연 방지 보장) |
| **역명 정규화 & 커버리지** | 도구마다 자유 문자열 입력, 프롬프트 의존 | `resolve_station`에서 별칭/유사도 매칭 및 1~8호선 커버리지 판정 선행 |
| **환각 억제** | "도구 결과만 써라" 시스템 프롬프트 의존 | 도구 강제(`tool_choice`) + **답변 내 시각/역명 백엔드 후처리 교차 검증** |
| **장애 대응** | 비기능 요구사항 부재 | LLM 타임아웃(2.5s) 및 장애 시 **규칙 기반 Python 엔진으로 즉시 폴백** |

### 3.2 산출 문서 목록
1. **기술 상세 명세서**: `AI/docs/AI_DEVELOPMENT_SPEC.md`
2. **AI 파트 인덱스 문서**: `AI/README.md`
3. **아키텍처 결정 기록**: `docs/decisions/001-ai-architecture-and-tool-design.md`
4. **프로젝트 상태 갱신**: `docs/PROJECT-STATE.md`

