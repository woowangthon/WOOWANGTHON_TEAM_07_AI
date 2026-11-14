# PROJECT-STATE.md

> 최종 갱신: 2026-11-14 (AI 파트 기준, BE 파트 PR #69·#70 반영). 각 레포의 상세 현황은 레포 안 문서가 원본이다.
> - AI: `AI/docs/IMPLEMENTATION_STATUS.md`, `AI/docs/BENCHMARK.md`, `AI/docs/DATA_SPEC.md` §9
> - BE: `BE/README.md`, `BE/docs/api-contract.md`, `BE/docs/frontend-integration.md`, `BE/docs/demo-handoff.md`

## 현재 프로젝트 개요
- **프로젝트명**: 우왕톤 7팀 (수도권 지하철 엘리베이터 상태 기반 대체 경로 & AI 출발 안내 서비스)
- **AI 현재 상태** (11-14 갱신 아래 "최신 상태" 참고): `/api/chat` 오케스트레이터(PR #15 머지), 50문항 벤치마크(PR #16), 응답 속도 옵션·폴백 수정·CORS(PR #19)는 리뷰 대기. BE API 데이터 소스와 BE 상태 규칙 반영(브랜치 `feat/be-integration`, 커밋 전)으로 역·엘리베이터·대체역을 BE에서 읽는다. 단위 테스트 217건 통과.
- **BE 현재 상태** (2026-11-14, `main` 0b251f7, PR #69·#70 머지): 역 검색·상세, 역별·전체 엘리베이터 상태(`line` 필터·`lines`), 대체역(BFS), **경로(`GET /api/routes`, 역 간 2분·환승 5분 추정)**, 출발 시각(시간표 미사용 추정, 대기 기본 5분, `from/to` 역 id로 경로 경고), 실시간 도착(외부 출처 미연동, 항상 UNCONFIRMED) API 구현. 계약 문서 `docs/api-contract.md`(8.4절 경로, 결정 D14~D18, FE/AI 리뷰 중, BE #14 Open). 데모 DB: 역 240개(1호선은 10개 역), 엘리베이터 상태는 11-13 샘플이라 대부분 STALE, 합성 설비 5건.

## 최신 상태 (2026-11-14 밤)
- **BE**: PR #69가 `main`에 머지됨(5d2b26a). 경로 API(`GET /api/routes`), 시간표 수집, 출발 시각 대기 기본 5분, 상태 목록 `line` 필터, 출발 시각의 `from/to` 경고, Windows 실행 수정 포함. BE `AGENTS.md`(PR #70)도 머지됨.
- **AI**: PR #16·#19·#20·#21(BE 출발 시각 연동·`data_updated_at` 수정·통합 시나리오 테스트)·#22(팀 결정 반영)가 모두 `main`에 머지됨. 열린 PR 없음. 열린 이슈: #1, #6, #12, #14, #17.
- **팀 결정(11-14)**: 대기 5분 통일, 모델·옵션 `gemini-3.5-flash-lite` + 사전 확인 + 템플릿 답변(기본값 변경), 역 목록 현재 방식 유지, BE 경로 API 전에는 경로·출발 시각 질문 막기(`BE_ROUTE_ESTIMATE=false`).
- **새 요구**: 시연·실서비스에서 **Mock 데이터 금지**(개발용 테스트 데이터는 허용, FE는 다른 담당자가 처리). AI 쪽 구현은 시작했다가 다른 작업과 겹쳐 **롤백했고 미착수**.
- **로컬 설정**: `AI/.env`의 `FACTCHAT_MODEL`은 `gemini-3.5-flash-lite`로 바뀌어 새 기본값이 적용됨 (확인 완료). 다른 팀원의 `.env`에 `gpt-5.1`이 남아 있으면 바꿔야 함.

### 다음 세션에서 이어갈 순서
1. AI: BE 경로 API 연동(AI #6, #12), 경로 질문 막기 해제, 출발 시각이 BE 기본 5분을 따르게 정리 (BE #69 머지로 가능해짐)
2. AI: BE `line` 필터와 출발 시각 `from/to` 경고 연동
3. AI: BE 데이터 기준 평가 문항 10개 추가
4. 전체: Mock 금지 (`AI_DATA_SOURCE` 기본값 be, 서비스 경로에서 Mock 차단, 응답에 데이터 출처 표시, 최종적으로 Mock 코드 제거). 팀원 작업과 겹치지 않는 시점에
5. 확인 필요: BE 시연 데이터의 합성 설비(`[DEMO/SYNTHETIC]` 5건)가 Mock 금지 규칙에 걸리는지, BE 실제 수집(#44)

## 이번에 한 일 (BE, 2026-11-14, PR #69)
- **팀 결정 반영**: 서울 열린데이터광장 평문 HTTP 기본 허용(D14), `buffer_minutes` 기본 5분(D15), 인접 간선은 시간표(OA-101)에서 같은 열차의 정차 순서로 추출해 저장(D16), 출발 시각의 `travel_minutes`는 외부 입력 유지·`from/to`로 경로 경고(D17), 상태 목록 `line` 필터(D18). 시연 서버는 집 데스크탑에서 터미널 실행(`BE/README.md` "시연 서버 실행").
- **구현**: 경로 API·엘리베이터 경고(`WARNING`/`UNCONFIRMED`/`CLEAR`), `train_schedules`·`collect_timetable`·`derive_edges`, Windows 실행 오류 수정(tzdata, `alembic.ini` ASCII). pytest 1055 통과.
- **FE/AI 영향(breaking)**: 출발 시각 `elevator_warning.items`가 객체 배열로, `status`가 3종으로 바뀜. `buffer_minutes` 기본 0→5. 상태 목록 설비에 `lines` 추가.
- **이슈**: #36, #37, #42 닫힘. #14, #16, #30, #31, #34, #35, #38, #39, #43, #44, #45는 진행 코멘트만 남기고 Open.
- **미검증**: 실제 시간표 수집·인접 간선 추출은 실행하지 않음(1호선 분기, 2호선 지선 불확실). 시간표 기반 탑승 열차 선택은 API에 연결하지 않음(#38). 수집 호출 한도 미확인.
- **도구 설정**: `deep-research` 스킬의 서브에이전트 호출 2곳에 `model="sonnet"` 추가(사용량 절감). 동기화되면 사라질 수 있어 `grep -c 'model="sonnet"' SKILL.md`로 2인지 확인.

## 이번에 한 일 (AI)
- **이슈 정리**: BE 이슈 43개를 바탕으로 AI 이슈 14개(AI #1~#14) 생성.
- **`/api/chat` 오케스트레이터** (AI #3, #7, #8, #9, #11 → PR #15 머지). 실행: `python AI/scripts/run_server.py` (포트 8001)
- **50문항 벤치마크** (AI #13 → PR #16): `gpt-5.1` 30초 50/50, 운영 3초 47/50, P50 5.5초.
- **응답 속도 개선** (AI #17, #18 → PR #19): 역 사전 확인·템플릿 답변 옵션(기본 끔), 폴백 기준선 50/50, CORS.

| 측정 (운영 타임아웃 3초, Mock 데이터) | 통과 | 타임아웃 폴백 | P50 / P95 |
|---|---|---|---|
| `gpt-5.1`, 옵션 끔 (현재 기본) | 47/50 | 22/50 | 5.6 / 6.5초 |
| `gpt-5.1`, 두 옵션 켬 | 49/50 | 2/50 | 1.6 / 3.0초 |
| `gemini-3.5-flash-lite`, 두 옵션 켬 | 50/50 | 0/50 | 1.0 / 1.5초 (11-14 재측정 1.06 / 1.83초) |

- **BE 계약 리뷰** (AI #1, 2026-11-14): BE #14에 코멘트. R9·R10 답변, `buffer_minutes` 0분 의견, 역명 괄호 표기·부분 일치 검색 처리, 엘리베이터 상태 목록의 노선 필터 요청, Windows 실행 문제(tzdata 없음, `alembic.ini` cp949 오류) 제보.
- **BE 상태 규칙과 BE 연동** (AI #2, #4, #5, #10 일부, 브랜치 `feat/be-integration`):
  - 도구 출력을 BE 규칙에 맞춤: 미확인 설비(`UNKNOWN`), `data_fresh`, `limitations`. 미확인을 정상으로 말하지 않고, `limitations`는 LLM 문장에도 코드로 덧붙인다.
  - `AI_DATA_SOURCE=be`로 BE API를 읽는다. BE 데모 서버에 붙여 폴백·`gemini-3.5-flash-lite` 모두 동작 확인 (첫 요청 3.5초, 이후 1.0~1.2초).
  - BE 장애 시 "확인할 수 없음"으로 답한다 (Mock·이전 값 사용 안 함).

## 주요 결정 사항
- **LLM 게이트웨이**: 학교 마인드로직 FactChat 게이트웨이 사용 (기본 모델 `gpt-5.1`)
- **보안**: API 키는 `AI/.env`에만 둔다. `.env.example`은 git에 올라가므로 키를 넣지 않는다.
- **카드·액션은 코드가 도구 결과로 조립**하고, LLM은 해석만 한다. 근거 없는 역명·시각은 후처리 검증으로 막는다.
- **AI는 BE API를 호출하고 대체역·출발 시각 계산은 BE** (2026-11-14 확정: BE가 API를 구현하고 `demo-handoff.md`에서 AI 입력으로 지정). 상태 TTL·역 ID·상태 표기는 BE 계약을 따른다.

## 결정이 필요한 것 (팀)
- **LLM 모델과 응답 속도 옵션** (AI #17): `gemini-3.5-flash-lite` + 두 옵션만 P95 2초 목표 안. 모델 전환 여부, 옵션 기본값, 타임아웃 기준.
- ~~출발 시각 대기 시간~~: 5분으로 통일해 BE에 반영됨(D15).
- LLM 타임아웃 값(명세 2.5초 vs 현재 3.0초), 대체역 우선순위, "실시간" 표현.

## 다음 할 일
- AI: `plan_trip`을 BE 경로·출발 시각 API로 교체(AI #6, #12). BE 경로 API(#69 머지)로 가능해짐.
- AI: FE `/api/chat` 연동(AI #14) — FE 쪽 호출 코드가 아직 없음.
- BE: **시연 PC에서 실제 수집 1회**(`collect_elevators --apply`, `collect_timetable --apply`, `derive_edges --apply`) 후 결과 기록 → #30, #34, #44, #45 닫기. 추출된 간선이 실제 1~8호선에서 맞는지 검증(#16, #31).
- BE: 시간표 기반 탑승 열차 선택 연결(#38), FE 리뷰 반영 후 계약 확정(#14).
