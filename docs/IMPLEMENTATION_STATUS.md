# AI 파트 구현 현황 및 동작 상세 (Implementation Status)

> 이 문서는 **현재 코드가 실제로 어떻게 동작하는지**를 정리한다. 목표 설계는 [`AI_DEVELOPMENT_SPEC.md`](./AI_DEVELOPMENT_SPEC.md)에 있다.
> 코드 동작을 바꾸면 이 문서의 해당 절도 같이 고친다.
>
> 최종 갱신: 2026-11-13 · 단위 테스트 68건 통과

---

## 1. 구현 상태

| 구성 요소 | 명세 | 상태 | 파일 |
|---|---|---|---|
| 도구 4종 입출력 스키마 | §3 | ✅ | `src/models.py` |
| 도구 4종 로직 | §3 | ✅ Mock 데이터 기반 | `src/tools/station.py`, `elevator.py`, `trip.py`, `src/data/mock_data.py` |
| LLM용 도구 정의 + 실행기 | §3 | ✅ | `src/tools/registry.py` |
| 시스템 프롬프트 | §5.1 | ✅ 템플릿 | `src/llm/prompts.py` |
| 규칙 기반 폴백 엔진 | §6.2 | ✅ | `src/fallback/engine.py` |
| 환경 설정 (`.env` 자동 로딩) | — | ✅ | `src/config.py` |
| LLM 게이트웨이 왕복 데모 | §9 Phase 2 | ✅ 실험용 | `scripts/llm_demo.py` |
| `/api/chat` 요청/응답 모델 | §4 | ✅ 스키마만 | `src/models.py` |
| FastAPI `/api/chat` 엔드포인트 (오케스트레이터) | §4 | ⏳ 미구현 | — |
| 세션 슬롯 메모리 (TTL 30분) | §5.2 | ⏳ 미구현 (`SessionContext` 모델 없음) | — |
| 타임아웃 → 폴백 전환 | §6.2 | ⏳ 미구현 (설정값만 있음) | — |
| 후처리 환각 검증기 (Guard) | §6.1 | ⏳ 미구현 | — |
| 50문항 벤치마크 | §8 | ⏳ 미구현 | — |
| 백엔드 실데이터·그래프 연동 | §7 | ⏳ BE 대기 | — |

---

## 2. 도구별 실제 동작

LLM은 `tools.registry.execute_tool(name, args)`로 도구를 실행한다. 인자를 `*Input` 모델로 검증하고, 실패하거나 도구가 `ValueError`를 내면 예외 대신 `{"error": "..."}`를 돌려준다.

### 2.1 `resolve_station(query)`

입력에서 공백과 노선 표기(`2호선`, `(1호선)`)를 지우고 끝의 `역`을 뗀 뒤, 처음 걸리는 규칙을 반환한다.

| 순서 | 규칙 | coverage | confidence |
|---|---|---|---|
| 1 | 표준 역명 또는 역 ID와 정확히 일치 | SUPPORTED | 1.0 |
| 2 | 별칭(`ALIASES`) 일치 — 예: 홍대, 고터, 동역사, 신천 | SUPPORTED | 0.95 |
| 3 | 미지원 키워드 포함 (`OUT_OF_COVERAGE_KEYWORDS`: 판교, 신분당선, 9호선, KTX 등) | OUT_OF_COVERAGE (`station_id=None`) | 0.9 |
| 4 | 표준 역명의 **앞부분**과 일치하는 역이 **정확히 하나** (2글자 이상) — 예: 동대문역사 | SUPPORTED | 0.8 |
| 5 | 그 외. 후보가 여러 개인 경우(예: 을지로)와 1글자 입력 포함 | NOT_FOUND | 0.0 |

- 커버리지는 "`STATIONS`에 등록된 역 = 지원"이다. 1~8호선 역이라도 Mock 데이터에 없으면 `NOT_FOUND`가 된다 (예: 부평).

### 2.2 `get_elevator_status(station_id)`

- `overall_status`는 고장 구간 **개수**로 정한다: 0개 → `AVAILABLE`, 전부 → `UNAVAILABLE`, 그 외 → `PARTIAL`. `MAINTENANCE`도 고장으로 센다.
- 엘리베이터 데이터가 있는 역은 9곳(`ELEVATORS`). 나머지 역과 모르는 ID는 오류 없이 `UNKNOWN`.
- `updated_at`은 "현재 − 5분", `freshness_minutes`는 5로 고정된 Mock 값이다.
- 현재 Mock 상 고장 역: 사당(2호선 승강장), 신림(6번 출구), 신도림(3번 출구) — 모두 `PARTIAL`.

### 2.3 `get_broken_elevators(line_id=None)`

- 고장 구간이 있는 역만 모은다. `line_id`가 있으면 그 노선이 지나는 역만.
- 정렬: `UNAVAILABLE` 먼저, 그다음 고장 구간 수 내림차순. `details`는 고장 구간 원본 dict 목록.

### 2.4 `plan_trip(start_id, end_id, arrive_by=None, depart_at=None, home_walk_min=7, *, now=None)`

**입력 검증**: 모르는 역 ID, `HH:MM`이 아닌 시각은 `ValueError`. `now`는 LLM에 노출되지 않는 인자로, 테스트나 요청의 `current_time`을 넣을 때 쓴다.

**경로**

- 두 역이 모두 2호선 순환선(`LINE_2_LOOP`, 43역)에 있으면 외선·내선 중 짧은 쪽. 같으면 외선(시계 방향).
- 하나라도 순환선 밖(서울역, 종로3가, 고속터미널)이면 `is_possible=False`, 경로 `[start, end]`, 정거장 0, `warnings[0]`에 사유. 이때 시간 값은 의미가 없다.
- 환승은 계산하지 않는다 (`transfers=0`, leg 1개).

**시간 (단위: 분)**

```text
총 소요 = 도보(home_walk_min, 기본 7) + 역 진입·엘리베이터(3) + 최악 대기(배차 3 + 여유 2 = 5)
        + 정거장 수 × 2 + 출구 엘리베이터(3)
예) 신도림→강남 12개 역: 7 + 3 + 5 + 24 + 3 = 42분
```

| 모드 | 조건 | 계산 |
|---|---|---|
| 역산 | `arrive_by` 있음 | 도착 = 오늘 `arrive_by` (이미 지났으면 **내일**). 출발 = 도착 − 총 소요. `is_late` = 출발 시각이 이미 지남 |
| 순방향 | `arrive_by` 없음 | 출발 = 오늘 `depart_at` 또는 현재. 도착 = 출발 + 총 소요. `is_late`는 항상 False |

`board_time` = 출발 + (도보 + 진입 + 대기). `minutes_from_now` = 출발 − 현재.

**엘리베이터 우회**

- 출발역과 도착역(같은 역이면 한 번)의 상태가 `PARTIAL`/`UNAVAILABLE`이면 `find_alternative`로 대체역을 찾아 `detours`(`target_type` = `start`/`end`)와 `warnings`에 넣는다.
  1. `ALTERNATIVES`에 등록된 안내가 있으면 사용 (거리·도보 시간 포함, 없으면 기본 800m / 10분).
  2. 없으면 순환선 양옆 역 중 고장이 확인되지 않은 역 (기본 800m / 10분).
  3. 대체역 자체가 고장으로 확인되면 추천하지 않는다.
- 환승역은 검사하지 않는다. 대체역을 찾아도 경로·시간은 다시 계산하지 않는다.
- **고장 역의 대체역만 알고 싶으면 `start_id == end_id`로 호출한다** (폴백 엔진과 LLM 도구 설명 모두 이 방식).

---

## 3. 폴백 엔진 (`FallbackEngine.process`)

### 3.1 처리 순서

1. `extract_mentions`: 문장에서 역을 찾는다.
   - 역명·별칭 사전을 **긴 표기부터** 문자열 매칭 (서울대입구 > 서울). 등장 순서대로, 중복 제거.
   - `서울`, `이대`는 뒤에 `역`이 붙어야 인정 (일반 단어와 겹침).
   - 매칭 뒤에 다른 글자가 이어지고 `역`으로 끝나면 더 긴 다른 역으로 보고 버린다 (강남구청역 ≠ 강남).
   - 미지원 키워드(`unsupported`)와, 사전에 없는 `○○역` 표기(`unknown`)도 따로 모은다.
2. `parse_korean_time`으로 시각 추출 (§3.3).
3. `classify_intent`로 의도 결정 (§3.2).
4. 미지원 키워드가 있으면 즉시 `out_of_coverage` 응답. **다른 역으로 대신 답하지 않는다.**
5. 역이 없고 "거기/그 역/여기…" 같은 지칭어가 있으면 `session_ctx["last_station_id"]` 사용.
6. 의도별 응답 (§3.4). 필요한 역이 없으면 추측하지 않고 `need_station`으로 되묻는다.

`session_ctx`는 읽기만 한다. 응답 후 세션 갱신은 호출하는 쪽(오케스트레이터) 책임이다. `current_time`("HH:MM")을 주면 그 시각 기준으로 계산한다.

### 3.2 의도 분류 점수표

| 조건 | 가점 |
|---|---|
| 강한 출발 표현 (`출발`, `몇 시에`, `나가야`, `늦지 않게` 등) | departure +4 |
| 약한 출발 표현 (`까지 가려면`, `도착하려면` 등) | 시각 있으면 departure +4, 없으면 route +3 |
| 시각 있음 + (`까지`/`도착`/`가려면`/`에 가`) | departure +3 |
| 대체/고장 표현 (`고장`, `안 돼`, `우회`, `다른 역`, `어디로` 등) | alternative +3 |
| `대체역` 또는 `우회` 포함 | alternative +3 (추가) |
| 고장 목록 표현 (`고장 난 역`, `현황`, `목록` 등), 역 언급 없을 때만 | outages +6 |
| 경로 표현 (`가는 길`, `경로`, `환승`, `가려면` 등) | route +3 |
| 역 2개 이상 | route +1 |
| 시각 있음 | route −2 |
| 엘리베이터 표현 (`엘베`, `휠체어`, `상태`, `탈 수` 등) | elevator +2 |
| 인사 표현, 역 언급 없을 때만 | greeting +2 |
| outages 점수 > 0 | alternative −2 |

최고점이 0 이하이면 `elevator`.

### 3.3 시각 파싱

- `HH:MM` 그대로 (범위 밖이면 None), `정오` 12:00, `자정` 00:00.
- `N시`, `N시 M분`, `N시 반` (N은 숫자 또는 한~열두). `3시간`, `네 시청역`처럼 뒤에 `간`/`청`이 오면 시각이 아니다.
- `오후/저녁/밤` → +12, `오전/아침/새벽` 12시 → 0시.
- **표시 없이 1~6시면 오후** (3시 반 → 15:30), 7시 이상은 그대로 (7시 → 07:00).

### 3.4 응답 종류 (`card_type`)

| card_type | 언제 | 호출 도구 | 액션 |
|---|---|---|---|
| `help` | 인사, 역 없음 | — | — |
| `outages_list` / `outages_empty` | 고장 목록 의도 | `get_broken_elevators` | 상위 3역 `openStation` |
| `out_of_coverage` | 미지원 지역/노선 언급 | (`resolve_station` 기록만) | — |
| `need_station` | 필요한 역을 알 수 없음, 사전에 없는 역, 출발=도착 | — | — |
| `trip_plan` | departure/route | `resolve_station`, `plan_trip` | `openTime`, 대체역마다 `showAlternative` |
| `trip_unsupported` | `plan_trip`이 `is_possible=False` | 〃 | — |
| `elevator_status` | elevator/alternative | `resolve_station`, `get_elevator_status`, 고장이면 `plan_trip(sid, sid)` | 대체역 있으면 `showAlternative`, `openStation` |

- departure/route의 출발·도착: 역 2개 → 앞이 출발. 역 1개 → 도착역, 출발은 `last_station_id` 또는 신도림(이 경우 "출발역이 지정되지 않아 신도림역 기준으로 안내합니다" 문구 포함).
- `tool_calls`에는 실제로 호출한 도구와 인자를 그대로 기록한다.
- `metadata.latency_ms`는 0 (측정은 오케스트레이터 몫).

---

## 4. 명세와 현재 구현의 차이

| 항목 | 명세 / 설계 검토 | 현재 구현 |
|---|---|---|
| LLM 타임아웃 | 2.5초 (명세 §1, §6.2) | `config.py` 기본값 3.0초 — **팀 확정 필요** |
| 커버리지 판정 | 역 단위 목록 (설계 검토 §2.2) | `STATIONS` 등록 여부 + 키워드 블랙리스트 |
| 엘리베이터 이용 가능 판정 | 이동 방향에 필요한 구간 기준 (설계 검토 §2.3) | 고장 구간 개수 기준 |
| 고장 우회 | 출발·환승·도착, 그래프에서 고장 노드 제외 | 출발·도착만, 등록된 안내 또는 인접 역 |
| 경로 탐색 | 다익스트라, 환승 포함 | 2호선 순환선 방향 비교만 |
| 오전/오후 모호성 | 현재 시각 이후 가장 가까운 시각 (설계 검토 §2.1) | 1~6시는 오후 고정, 지난 시각은 다음 날 |
| 첫차/막차 | 범위 밖이면 예외 응답 (설계 검토 §2.4) | 미반영 |
| 데이터 신선도 | 실제 갱신 시각 | 현재 − 5분 고정값 |

---

## 5. 남은 제약 (Mock 단계라 의도적으로 둔 것)

- 경로는 2호선 순환선만. 순환선 밖 역은 `is_possible=False`로 정직하게 거절한다.
- 대체역 거리 정보가 없는 곳(방배, 신대방, 인접 역 자동 선택)은 기본값 800m / 10분.
- 폴백의 역 추출은 띄어쓰기가 없는 문장("신도림에서강남역")에서 앞 역을 놓칠 수 있다. LLM 경로는 `resolve_station`이 역 이름만 받으므로 영향 없음.

---

## 6. 변경 이력

### 2026-11-13 — 디렉토리 구조 정리 (동작 변경 없음)

`src/`의 평평한 모듈 8개를 역할별 하위 패키지로 나누고, 테스트도 같은 구조로 옮겼다. 아래 이력의 옛 경로는 당시 기준이다.

| 이전 | 이후 |
|---|---|
| `src/mock_data.py` | `src/data/mock_data.py` |
| `src/tools.py` | `src/tools/station.py`, `elevator.py`, `trip.py`, `common.py`로 분리 |
| `src/tool_registry.py` | `src/tools/registry.py` |
| `src/prompts.py` | `src/llm/prompts.py` |
| `src/fallback_engine.py` | `src/fallback/engine.py` |
| `tests/test_tools.py` | `tests/tools/test_station.py`, `test_elevator.py`, `test_trip.py`로 분리 |
| `tests/test_tool_registry.py` | `tests/tools/test_registry.py` |
| `tests/test_fallback.py` | `tests/fallback/test_engine.py` |
| `tests/test_mock_data.py` | `tests/data/test_mock_data.py` |

공개 import 경로: `from AI.src.tools import plan_trip`(도구), `from AI.src.tools.registry import TOOL_SPECS`, `from AI.src.fallback import FallbackEngine`, `from AI.src.llm.prompts import build_system_prompt`. `from AI.src import ...`도 그대로 동작한다.

### 2026-11-12 — 버그 수정 및 구조 정리

| 문제 | 조치 |
|---|---|
| 대체역 이름이 ID로 노출 ("대체역(mullae역)") | 2호선 순환선 43역을 모두 `STATIONS`에 등록, 데이터 무결성 테스트 추가 |
| 폴백에서 미지원 역(판교)을 물으면 강남역으로 답함 | `out_of_coverage` 응답 추가, 역이 없으면 기본값 대신 `need_station`으로 되물음 |
| 역명 부분 일치가 느슨함 ("서울"→서울대입구, "강"→강남) | 정확 일치 우선, 접두어 일치는 후보가 하나일 때만. `서울` 표준명 정리 |
| "서울역역" 문구 | 표준 역명에서 '역' 제거 |
| `arrive_by` 형식 오류는 무시, `depart_at`은 예외 | 둘 다 `ValueError`, 모델 단계에서 `HH:MM` 패턴 검증 |
| 순환선 밖 역을 "1정거장"으로 계산 | `is_possible=False` + 사유 경고 |
| 출발역 고장 시 대체역 없음 | 출발역도 대체역 탐색 (`target_type="start"`) |
| 폴백 `tool_calls` 기록이 실제 호출과 다름 | 실제 인자 그대로 기록 |
| 폴백이 `current_time`을 무시 | 기준 시각으로 사용 |
| "3시간", "네 시청역"을 시각으로 인식, "25:00" 통과 | 시각 정규식·범위 검증 보강 |
| 도구 스키마가 스크립트마다 손으로 중복 작성 (일부 누락) | `tool_registry.py`에서 모델로부터 자동 생성 |
| `nvidiaApi.py`는 import만 해도 API 호출, 스크립트 2개 중복 | `scripts/llm_demo.py`(`--provider factchat` / `nvidia`)로 통합, 기존 파일 삭제 |
| `.env`를 읽지 않음, 모델 기본값 불일치 | `config.py`가 `AI/.env` 자동 로딩, 기본 모델 `gpt-5.1`로 통일 |
| Windows 콘솔 이모지 출력 오류 | 데모 스크립트가 stdout을 UTF-8로 설정 |
| 테스트를 `AI/` 안에서 돌리면 import 실패 | `conftest.py`, `pytest.ini` 추가 |
| 설계 검토 문서가 레포 루트에 한글 파일명으로 있음 | `docs/DESIGN_REVIEW.md`로 이동 |
