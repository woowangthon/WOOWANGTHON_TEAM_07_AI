# AGENTS.md — AI 에이전트 작업 가이드

이 레포에서 작업하는 AI 코딩 에이전트(Claude, Gemini, Codex 등)가 **가장 먼저** 읽는 문서다.
사람용 소개는 [README.md](README.md)에 있다.

## 1. 이 레포는 무엇인가

우왕톤 7팀 "엘리베이터 지하철" 서비스의 **AI 파트**. 휠체어·유모차 이용자에게 지하철 엘리베이터 상태, 고장 시 대체역, 늦지 않는 출발 시각을 챗봇으로 안내한다.
LLM이 사용자 말을 해석해 도구를 호출하고, 계산은 Python 도구가 한다. LLM이 실패하면 규칙 기반 폴백 엔진이 같은 형식으로 답한다.

현재 단계: Phase 1 완료(도구·폴백·테스트). Phase 2·3의 AI 내부 작업(`/api/chat` 오케스트레이터, 세션, 폴백 전환, 후처리 검증)과 50문항 벤치마크 구현. `gpt-5.1`의 응답 시간(P50 약 5초)이 목표를 넘는 문제와 BE API 연동이 남음.
상세 현황: [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)

## 2. 읽는 순서

1. 이 문서
2. [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) — 지금 코드가 실제로 어떻게 동작하는가
3. [docs/AI_DEVELOPMENT_SPEC.md](docs/AI_DEVELOPMENT_SPEC.md) — 목표 설계와 FE/BE와의 계약(도구 스키마, `/api/chat`)
4. [docs/DATA_SPEC.md](docs/DATA_SPEC.md) — 외부 데이터셋 명세, 품질 문제, 도구 스키마와의 대응 (데이터 작업 시 필수)
   - [docs/BENCHMARK.md](docs/BENCHMARK.md) — 50문항 평가 방법과 결과 (프롬프트·폴백을 고칠 때)
5. [docs/DESIGN_REVIEW.md](docs/DESIGN_REVIEW.md) — 왜 이렇게 설계했는가 (기록용)

명세와 코드가 다르면 **코드와 IMPLEMENTATION_STATUS가 현재 사실**이고, 명세는 목표다. 차이를 발견하면 IMPLEMENTATION_STATUS §5에 적는다.

## 3. 반드시 지킬 원칙

이 서비스에서 틀린 정보는 휠체어 이용자가 열차를 놓치거나 고장 난 역에 갇히는 결과로 이어진다.

1. **LLM은 해석만, 계산은 도구가.** 경로·시간·엘리베이터 상태를 LLM 출력이나 프롬프트로 만들지 않는다. 새 계산이 필요하면 `src/tools/`에 도구 로직으로 넣는다.
2. **액션 데이터(`actions`, 카드)는 백엔드 코드가 도구 결과로 직접 조립한다.** LLM이 JSON을 쓰게 하지 않는다.
3. **모르면 모른다고 한다.** 역을 못 찾거나 지원 범위 밖이면 기본 역으로 대신 답하지 말고 되묻거나 거절한다. 계산할 수 없는 구간은 `is_possible=False`로 돌려준다.
4. **시간은 보수적으로.** 대기 시간은 평균(배차/2)이 아니라 배차 간격 + 안전 여유로 잡는다.
5. **도구 입출력 스키마는 FE/BE와의 계약이다.** `src/models.py`를 바꾸면 명세서 §3·§4도 같이 고친다.

## 4. 명령어

모든 명령은 `AI/` 안에서 실행해도 되고, 부모 폴더에서 `AI/...` 경로로 실행해도 된다.

```powershell
pip install -r requirements.txt          # pydantic, openai, fastapi, uvicorn, httpx, pytest
python -m pytest                         # 전체 테스트 (167건). 작업 전후로 반드시 실행
python scripts/run_server.py             # /api/chat 개발 서버 (http://127.0.0.1:8001, 키 없으면 폴백으로 응답)
python scripts/llm_demo.py "사당역 엘리베이터 돼?"              # FactChat 게이트웨이 왕복 데모 (API 키 필요)
python scripts/llm_demo.py --provider nvidia "지금 고장난 역 알려줘"
python scripts/check_raw_data.py         # data/raw/ 원본 검증 (인코딩·헤더·행 수·값 분포)
python scripts/run_benchmark.py          # 50문항 벤치마크 (기본 폴백 기준선, --engine llm은 키 필요)
```

API 키는 `.env.example`을 `.env`로 복사해 채운다. `src/config.py`가 자동으로 읽는다. **키를 코드나 문서에 쓰지 않는다.**

## 5. 코드 지도 — 무엇을 바꾸려면 어디를 보는가

| 바꾸려는 것 | 파일 | 같이 고칠 것 |
|---|---|---|
| 도구 입출력 필드 | `src/models.py` | 명세 §3, `tests/tools/test_registry.py` |
| 역 이름 정규화 | `src/tools/station.py` | `tests/tools/test_station.py`, IMPLEMENTATION_STATUS §2.1 |
| 엘리베이터 상태·고장 목록 | `src/tools/elevator.py` | `tests/tools/test_elevator.py`, IMPLEMENTATION_STATUS §2.2–2.3 |
| 경로·시간·대체역 계산 | `src/tools/trip.py` | `tests/tools/test_trip.py`, IMPLEMENTATION_STATUS §2.4 |
| 역·별칭·엘리베이터·대체역·시간 상수 | `src/data/mock_data.py` | `tests/data/test_mock_data.py`가 참조 무결성 검사 |
| 데이터 소스 인터페이스·구현 (Mock/BE) | `src/data/source.py`, `mock_source.py` | `tests/data/test_data_source.py`, IMPLEMENTATION_STATUS §4.6. 도구·폴백은 `mock_data`를 직접 import하지 않는다 |
| `/api/chat` 처리 흐름·폴백 전환 | `src/llm/orchestrator.py`, `client.py` | `tests/llm/test_orchestrator.py`, IMPLEMENTATION_STATUS §4.1·4.3 |
| 카드·액션 조립, 템플릿 문장 | `src/llm/response_builder.py` | IMPLEMENTATION_STATUS §4.2 |
| 세션 슬롯 | `src/llm/session.py` | `tests/llm/test_session.py`, IMPLEMENTATION_STATUS §4.4 |
| 후처리 환각 검증 | `src/llm/guard.py` | `tests/llm/test_guard.py`, IMPLEMENTATION_STATUS §4.5 |
| HTTP 엔드포인트 | `src/api/app.py` | `tests/api/test_chat_api.py`, 명세 §4 |
| 벤치마크 평가셋·채점 | `src/eval/cases.json`, `benchmark.py` | `tests/eval/test_benchmark.py`, `docs/BENCHMARK.md` (결과는 실제로 돌린 값만 기록) |
| LLM에 보이는 도구 설명 | `src/tools/registry.py` (`_TOOLS`) | 파라미터 스키마는 `models.py`에서 자동 생성되므로 손으로 쓰지 않는다 |
| 시스템 프롬프트 | `src/llm/prompts.py` | 명세 §5.1 (같은 문구 유지). 바꾼 뒤 `run_benchmark.py --engine llm`으로 재측정해 `docs/BENCHMARK.md`에 기록 |
| 폴백 의도 분류·문장 | `src/fallback/engine.py` | `tests/fallback/test_engine.py`, IMPLEMENTATION_STATUS §3 |
| 게이트웨이·타임아웃·왕복 횟수 설정 | `src/config.py`, `.env.example` | IMPLEMENTATION_STATUS §4.3 |
| 외부 데이터 원본 추가·교체 | `data/raw/`, `data/sources.json` | `docs/DATA_SPEC.md`, `scripts/check_raw_data.py`로 검증 |
| 실험용 실행 스크립트 | `scripts/` | — |

다음에 만들 것: BE API 데이터 소스 구현(`src/data/`에 추가, 이슈 #4~#6). BE 계약 문서(BE #8, #14)가 나온 뒤 진행한다. 오케스트레이터 테스트는 실제 LLM 대신 `tests/llm/fake_llm.py`의 `FakeLLM`을 쓴다.

## 6. 작업 규칙

- **동작을 바꾸면 테스트를 추가하고, IMPLEMENTATION_STATUS의 해당 절을 고친다.** 문서에 없는 동작을 만들지 않는다.
- 실행해서 확인하지 않은 것을 문서나 커밋 메시지에 "완료", "100% 준수"처럼 쓰지 않는다. 숫자(테스트 수, 소요 시간)는 실제로 돌려 본 값만 쓴다.
- Mock 데이터 규칙: 어디서든 참조하는 역 ID는 `STATIONS`에 등록한다. 역명(`name`)에는 `역`을 붙이지 않는다(문장에서 `f"{name}역"`).
- `src/` 모듈은 import할 때 네트워크 호출·출력 같은 부작용이 없어야 한다. 실행 스크립트는 `scripts/`에 두고 `if __name__ == "__main__":`으로 감싼다.
- import는 `AI.src.*` 절대 경로(테스트·스크립트) 또는 패키지 내부 상대 경로(`from .tools import ...`)를 쓴다. 레포 폴더 이름은 반드시 `AI`여야 한다.
- 파일 배치: `src/`는 역할별 하위 패키지(`data/`, `tools/`, `llm/`, `fallback/`, `api/`, `eval/`)로 나눈다. 여러 계층이 함께 쓰는 `config.py`, `models.py`만 `src/` 바로 아래에 둔다. 테스트는 `tests/` 아래에 `src/`와 같은 구조로 두고, 테스트 파일 이름은 프로젝트 전체에서 겹치지 않게 한다(`tests/`에 `__init__.py`가 없어 이름이 같으면 충돌함).
- 파일명: Python 모듈은 `snake_case.py`. `scripts/`에 `test_*.py`나 `*_test.py` 이름을 쓰지 않는다(pytest가 수집함).
- 데이터: `data/raw/`의 원본은 **절대 수정하지 않는다**(인코딩 포함). 다시 받으면 `raw/<제공처>/<받은 날짜>/`에 새로 넣고 `data/sources.json`을 고친다. 가공은 스크립트로 `data/processed/`에 만들며, 손으로 만든 값을 섞지 않는다. 원본 컬럼의 의미가 명세로 확인되지 않았으면 문서에 "추정"이라고 쓴다. 절차는 `docs/DATA_SPEC.md` §3.
- 인코딩: 직접 작성하는 텍스트 파일은 **UTF-8**. UTF-16으로 저장하면 git이 바이너리로 취급한다.
- 문서·주석·안내 문장은 한국어. 새 문서는 `docs/`에 만들고 레포 루트에 흩어 놓지 않는다.
- 커밋 메시지는 `feat:`, `fix:`, `docs:`, `refactor:`, `test:` 접두어. PR은 `.github/pull_request_template.md` 양식.

## 7. 아직 결정되지 않은 것 (임의로 정하지 말고 사용자에게 묻는다)

- LLM 타임아웃: 명세 2.5초 vs `config.py` 3.0초
- 사용할 LLM 모델: 현재 기본값 FactChat `gpt-5.1` (NVIDIA `z-ai/glm-5.3-flash`와 비교 중)
- 대체역 우선순위: 거리 vs 같은 노선 (설계 검토 §4)
- 서비스 문구의 "실시간" 표현 (운행 상태 원천 OA-15994는 매시 갱신), 휠체어 환승·이동 여유 시간 고정값, 상태 TTL, 역 ID 형식, 상태 표기 — `docs/DATA_SPEC.md` §8
- AI가 데이터를 읽는 방식과 경로·대체역 계산 위치는 AI 쪽 제안(BE API 호출, 계산은 BE)을 DATA_SPEC §8에 적었고 BE 확인을 기다린다. 확정 전까지 BE 구현을 전제로 한 코드를 만들지 않는다.

## 8. 레포 밖 관련 문서

이 레포(`AI/`)는 상위 프로젝트 폴더 안에 있으며, 형제 레포 `FE/`, `BE/`와 공용 `docs/`가 있다.

- `../docs/cards/004-ai-development-plan.md` — AI 파트 작업 카드
- `../docs/decisions/001-ai-architecture-and-tool-design.md` — 아키텍처 결정 기록
- `../docs/PROJECT-STATE.md` — 프로젝트 전체 진행 상황
- `../FE/Prototype-v2/js/ai-agent.js` — 폴백 엔진의 원본 프로토타입 로직
