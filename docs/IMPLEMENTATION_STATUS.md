# AI 파트 구현 현황 및 동작 상세 (Implementation Status)

> 이 문서는 **현재 코드가 실제로 어떻게 동작하는지**를 정리한다. 목표 설계는 [`AI_DEVELOPMENT_SPEC.md`](./AI_DEVELOPMENT_SPEC.md)에 있다.
> 코드 동작을 바꾸면 이 문서의 해당 절도 같이 고친다.
>
> 최종 갱신: 2026-11-13 · 단위 테스트 105건 통과

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
| `/api/chat` 요청/응답 모델 | §4 | ✅ | `src/models.py` |
| 데이터 소스 인터페이스 (Mock/BE 교체) | — | ✅ Mock 구현만 | `src/data/source.py`, `mock_source.py` |
| FastAPI `/api/chat` 엔드포인트 (오케스트레이터) | §4 | ✅ 가짜 LLM·폴백 경로로 검증. 실제 게이트웨이 왕복은 미확인 | `src/api/app.py`, `src/llm/orchestrator.py`, `client.py` |
| 액션·카드 조립 (도구 결과 기반) | §4 | ✅ | `src/llm/response_builder.py` |
| 세션 슬롯 메모리 (TTL 30분) | §5.2 | ✅ 인메모리 | `src/llm/session.py` |
| LLM 장애 → 폴백 전환 | §6.2 | ✅ | `src/llm/orchestrator.py` |
| 후처리 환각 검증기 (Guard) | §6.1 | ✅ 역명·시각만 | `src/llm/guard.py` |
| 50문항 벤치마크 | §8 | ⏳ 미구현 | — |
| 백엔드 실데이터·그래프 연동 | §7 | ⏳ 원본 4종 분석 완료, 4종(경로·시간표·빠른하차 등) BE 수집 예정, 연동 전 ([DATA_SPEC](./DATA_SPEC.md)) | `data/` |

---

## 2. 도구별 실제 동작

LLM은 `tools.registry.execute_tool(name, args, now=None)`로 도구를 실행한다. 인자를 `*Input` 모델로 검증하고, 실패하거나 도구가 `ValueError`를 내면 예외 대신 `{"error": "..."}`를 돌려준다. `now`는 `plan_trip`에만 전달되며 오케스트레이터가 요청의 `current_time`으로 채운다.

도구와 폴백 엔진은 `mock_data.py`를 직접 읽지 않고 데이터 소스 인터페이스(`data.get_data_source()`)를 거친다 (§4.6). 아래 동작은 기본 데이터 소스인 `MockDataSource` 기준이다.

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

## 4. 오케스트레이터 (`POST /api/chat`)

### 4.1 처리 흐름 (`ChatOrchestrator.handle`)

1. `session_id`로 세션 슬롯을 꺼내 시스템 프롬프트(`build_system_prompt`)에 최근 역·최근 경로를 넣는다.
2. LLM에 `TOOL_SPECS`와 함께 보낸다. 응답에 도구 호출이 있으면 `execute_tool`로 실행하고 결과를 `tool` 메시지로 붙여 다시 보낸다. 최대 `LLM_MAX_TOOL_ROUNDS`(기본 4)회.
3. 도구 호출 없이 문장이 오면 끝. `card_type`·`actions`·`suggestions`는 도구 결과로 코드가 만든다 (§4.2).
4. 후처리 검증(§4.5)에 걸리면 LLM 문장을 버리고 도구 결과로 만든 템플릿 문장을 쓴다.
5. 세션 슬롯을 갱신하고(§4.4) `metadata.latency_ms`를 기록한다.

- 대화 원문은 저장하지 않는다. 요청마다 시스템 프롬프트 + 이번 사용자 문장으로 새로 시작한다.
- `/api/chat`은 동기 함수이며 FastAPI 스레드 풀에서 실행된다. 명세의 `async/await`는 아직 쓰지 않는다.
- 실행: `python scripts/run_server.py` (기본 포트 8001). LLM 키가 없으면 모든 요청이 폴백으로 답한다.

### 4.2 카드·액션 조립 (`response_builder.build_response`)

도구 결과에서 아래 순서로 처음 해당하는 것을 고른다. `card_type` 이름은 폴백 엔진(§3.4)과 같다. 오류 결과(`{"error"}`)는 건너뛴다.

| 순서 | 근거 | card_type | 액션 |
|---|---|---|---|
| 1 | `plan_trip` (출발≠도착) | `trip_plan` / `trip_unsupported` | `openTime`, 대체역마다 `showAlternative` |
| 2 | `get_elevator_status` | `elevator_status` | 같은 역 `plan_trip(sid, sid)`에 대체역이 있으면 `showAlternative`, 그리고 `openStation` |
| 3 | `plan_trip` (출발=도착, 대체역만) | `elevator_status` | `showAlternative`, `openStation` |
| 4 | `get_broken_elevators` | `outages_list` / `outages_empty` | 상위 3역 `openStation` |
| 5 | `resolve_station` OUT_OF_COVERAGE | `out_of_coverage` | — |
| 6 | `resolve_station` NOT_FOUND | `need_station` | — |
| 7 | 도구 호출 없음 | `help` | — |

`metadata.data_updated_at`은 고른 결과에 `updated_at`이 있으면 그 값, 없으면 응답 생성 시각이다.

### 4.3 폴백 전환

다음 경우 `FallbackEngine.process(message, session_id, current_time, 세션 슬롯)`의 응답을 그대로 돌려준다. `metadata.engine="fallback"`, `fallback_used=true`, `fallback_reason`에 사유를 넣는다.

| fallback_reason | 조건 |
|---|---|
| `timeout` | 게이트웨이 호출 1회가 `LLM_TIMEOUT_SECONDS`(현재 3.0초)를 넘음. 재시도하지 않는다 |
| `rate_limit` | HTTP 429 |
| `network` | 연결 실패 |
| `server_error` / `api_error` | HTTP 5xx / 그 밖의 API 오류 (인증 실패 포함) |
| `not_configured` | LLM API 키 없음 |
| `empty_reply` | 도구 호출도 문장도 없는 응답 |
| `max_rounds` | 도구 왕복이 `LLM_MAX_TOOL_ROUNDS`를 넘음 |
| `internal_error` | 도구 실행·응답 조립 중 예기치 못한 예외 |

- 타임아웃은 **호출 1회 기준**이다. 도구 왕복이 여러 번이면 전체 응답 시간은 그보다 길어질 수 있다.

### 4.4 세션 슬롯 (`session.py`)

- `SessionContext`: `last_station_id`, `last_station_name`, `last_route`(`start_id`, `end_id`, `arrive_by`, 출발·도착 시각), `last_query_type`(= card_type), `updated_at`.
- `SessionStore`: 인메모리. 마지막 저장 후 `SESSION_TTL_MINUTES`(30분)가 지나면 버린다. 저장할 때마다 만료 세션을 지우고, `max_sessions`(10,000)를 넘으면 오래된 것부터 버린다. 서버를 재시작하면 사라진다.
- 최근 역 우선순위: 경로의 도착역 > 상태를 조회한 역 > 대체역을 찾은 역 > `resolve_station`으로 확인된 역. 데이터 소스에 없는 역 ID는 무시한다.
- 폴백 응답도 `tool_calls` 인자로 같은 규칙을 적용해 갱신한다. 그래서 LLM이 실패한 다음 요청에서도 "거기"가 이어진다.

### 4.5 후처리 검증 (`guard.check_text`)

LLM 문장에서 `○○역`(앞 2~10글자)과 시각(`HH:MM`, `N시 M분`, `N시 반`, `N시`)을 뽑아, 이번 요청의 근거에 있는지 확인한다.

- 근거: 도구 인자·결과의 모든 문자열(역 ID는 역명으로 바꿈), 사용자 문장, 기준 시각.
- 역명: 근거의 역명 중 하나로 **끝나면** 통과 ("인접한강남역"처럼 앞말이 붙어 잡힌 경우). "대체역", "해당역" 같은 일반 표현과 "지역", "구역"처럼 '역'으로 끝나는 명사는 검사하지 않는다.
- 시각: 근거 시각과 분이 같고 시가 12시간 차이 이내로 같으면 통과 ("3시" = 15:00).
- 하나라도 걸리면 `metadata.guard_triggered=true`, 문장은 §4.2의 템플릿 문장으로 바꾼다. 카드·액션은 그대로다.
- 검사하지 않는 것: '역' 없이 쓴 역명, 소요 시간·거리 숫자, 엘리베이터 위치 설명.

### 4.6 데이터 소스 (`data/source.py`)

- `DataSource` 인터페이스: 역 목록·역 하나·별칭·미지원 키워드, 역별 엘리베이터·전체 엘리베이터·기준 시각, 등록된 대체역·인접 역·경로, 시간 상수·대체역 기본 거리.
- 구현 선택: 환경 변수 `AI_DATA_SOURCE` (현재 `mock`만 지원). 테스트는 `use_data_source(가짜)`로 바꿔 끼운다.
- 상태 판정(`PARTIAL` 등), 출발 시각 역산, 안내 문장은 도구에 남아 있다. BE API 연동(이슈 #4~#6) 때 BE 구현을 추가하고, 경로·대체역은 BE 계산 결과를 받도록 바꾼다.

---

## 5. 명세와 현재 구현의 차이

| 항목 | 명세 / 설계 검토 | 현재 구현 |
|---|---|---|
| LLM 타임아웃 | 2.5초 (명세 §1, §6.2) | `config.py` 기본값 3.0초, 호출 1회 기준 — **팀 확정 필요** |
| 오케스트레이터 비동기 처리 | `async/await` (명세 §9 Phase 2) | 동기 함수 + FastAPI 스레드 풀 |
| 후처리 검증 대상 | 역명·시각 정규식 (명세 §6.1) | 같음. 추가로 `N시 반`, `N시` 형식과 오전/오후 12시간 차이 허용 (§4.5) |
| `ChatResponseMetadata` | `engine`, `latency_ms`, `fallback_used`, `data_updated_at` | `fallback_reason`, `guard_triggered` 추가 (명세 §4.2 반영) |
| 커버리지 판정 | 역 단위 목록 (설계 검토 §2.2) | `STATIONS` 등록 여부 + 키워드 블랙리스트 |
| 엘리베이터 이용 가능 판정 | 이동 방향에 필요한 구간 기준 (설계 검토 §2.3) | 고장 구간 개수 기준 |
| 고장 우회 | 출발·환승·도착, 그래프에서 고장 노드 제외 | 출발·도착만, 등록된 안내 또는 인접 역 |
| 경로 탐색 | 다익스트라, 환승 포함 | 2호선 순환선 방향 비교만 |
| 오전/오후 모호성 | 현재 시각 이후 가장 가까운 시각 (설계 검토 §2.1) | 1~6시는 오후 고정, 지난 시각은 다음 날 |
| 첫차/막차 | 범위 밖이면 예외 응답 (설계 검토 §2.4) | 미반영 |
| 데이터 신선도 | 실제 갱신 시각 | 현재 − 5분 고정값 |

---

## 6. 남은 제약 (Mock 단계라 의도적으로 둔 것)

- 경로는 2호선 순환선만. 순환선 밖 역은 `is_possible=False`로 정직하게 거절한다.
- 대체역 거리 정보가 없는 곳(방배, 신대방, 인접 역 자동 선택)은 기본값 800m / 10분.
- 폴백의 역 추출은 띄어쓰기가 없는 문장("신도림에서강남역")에서 앞 역을 놓칠 수 있다. LLM 경로는 `resolve_station`이 역 이름만 받으므로 영향 없음.
- 세션은 서버 프로세스 메모리에만 있다. 서버를 여러 대 띄우거나 재시작하면 이어지지 않는다.
- 실제 LLM 게이트웨이(FactChat/NVIDIA)로 `/api/chat`을 왕복한 결과는 아직 확인하지 않았다 (테스트는 가짜 LLM 사용).

---

## 7. 변경 이력

### 2026-11-13 — `/api/chat` 오케스트레이터, 폴백 전환, 세션, 후처리 검증, 데이터 소스 분리 (이슈 #3, #7, #8, #9, #11)

| 추가·변경 | 내용 |
|---|---|
| `src/data/source.py`, `mock_source.py` | 데이터 소스 인터페이스. 도구·폴백 엔진이 `mock_data.py` 대신 이를 거침. 동작 변화 없음 (기존 68건 그대로 통과) |
| `src/llm/client.py` | OpenAI 호환 클라이언트. 실패를 `LLMUnavailableError(kind)`로 통일, 재시도 없음 |
| `src/llm/orchestrator.py` | 도구 호출 루프, 폴백 전환, 후처리 검증, 세션 갱신, `latency_ms` |
| `src/llm/response_builder.py` | 도구 결과로 card_type·actions·suggestions·템플릿 문장 조립 |
| `src/llm/session.py` | `SessionContext`, TTL 인메모리 `SessionStore` |
| `src/llm/guard.py` | 역명·시각 후처리 검증 |
| `src/api/app.py`, `scripts/run_server.py` | FastAPI `POST /api/chat`, `GET /health` |
| `src/tools/registry.py` | `execute_tool(..., now=)` — 요청의 `current_time`을 `plan_trip`에 전달 |
| `src/models.py` | `ChatResponseMetadata.fallback_reason`, `guard_triggered` 추가 |
| `src/config.py` | `LLM_PROVIDER`, `LLM_MAX_TOOL_ROUNDS` 추가. 환경 변수 `AI_DATA_SOURCE` |
| `src/tools/common.py` | `mock_updated_at`, `MOCK_FRESHNESS_MIN` 제거 → `MockDataSource.data_updated_at` |
| 테스트 | `tests/data/test_data_source.py`, `tests/llm/`, `tests/api/` 37건 추가 (총 105건) |

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
