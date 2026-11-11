# AI 파트 구현 현황 및 동작 상세 (Implementation Status)

> 이 문서는 **현재 코드가 실제로 어떻게 동작하는지**를 정리한다. 목표 설계는 [`AI_DEVELOPMENT_SPEC.md`](./AI_DEVELOPMENT_SPEC.md)에 있고, 이 문서는 그 설계 대비 지금 어디까지 구현되었는지, 어떤 단순화와 이슈가 있는지를 다룬다.
>
> 기준 커밋: `df0f2a2` (2026-11-12) · 단위 테스트 11건 통과

---

## 1. 한눈에 보는 구현 상태

| 구성 요소 | 명세 위치 | 상태 | 구현 파일 |
|---|---|---|---|
| 도구 4종 입출력 스키마 | §3 | ✅ 완료 | `src/models.py` |
| 도구 4종 로직 | §3 | ✅ Mock 기반 완료 (실데이터/그래프 미연동) | `src/tools.py`, `src/mock_data.py` |
| 규칙 기반 폴백 엔진 | §6.2 | ✅ 완료 | `src/fallback_engine.py` |
| `/api/chat` 요청/응답 모델 | §4 | ✅ 스키마만 | `src/models.py` (`ChatRequest`, `ChatResponse`) |
| LLM 게이트웨이 연동 | §9 Phase 2 | 🔄 실험 스크립트만 | `src/schoolApi.py`, `src/nvidiaApi.py` |
| 환경 설정 | — | 🔄 작성됨, 미사용 | `src/config.py` |
| FastAPI `/api/chat` 엔드포인트 | §4 | ⏳ 미구현 | — |
| 세션 슬롯 메모리 (TTL 30분) | §5.2 | ⏳ 미구현 (`SessionContext` 모델도 아직 없음) | — |
| 시스템 프롬프트 동적 주입 | §5.1 | ⏳ 미구현 (실험 스크립트에 축약본만) | — |
| 후처리 환각 검증기 (Guard) | §6.1 | ⏳ 미구현 | — |
| 타임아웃 → 폴백 전환 | §6.2 | ⏳ 미구현 (설정값만 존재) | — |
| 50문항 벤치마크 | §8 | ⏳ 미구현 | — |

---

## 2. 모듈별 역할

| 모듈 | 역할 | 비고 |
|---|---|---|
| `models.py` | 도구 입출력, `ChatRequest`/`ChatResponse`, `ActionPayload` 등 Pydantic v2 모델 | `ActionPayload.type`은 `openStation` / `showAlternative` / `openTime` 세 가지만 허용 |
| `mock_data.py` | `LINES`(1~8호선 색상), `LINE_2_LOOP`(2호선 순환 43역), `STATIONS`(30역), `ALIASES`, `ELEVATORS`(9역), `ALTERNATIVE_TIPS`(5쌍), `TIMING_PARAMS` | `FE/Prototype-v2/js/mock-data.js` 기반 |
| `tools.py` | 도구 4종. 모두 `models.py`의 출력 모델 인스턴스를 반환 | LLM 연동 시 `.model_dump()`로 직렬화해 전달 |
| `fallback_engine.py` | 정규식 점수 기반 의도 분류 → 도구 호출 → 템플릿 문장 + 액션 데이터 조립 | `FE/Prototype-v2/js/ai-agent.js` 포팅 |
| `config.py` | `Settings` (FactChat 키/URL/모델, `LLM_TIMEOUT_SECONDS=3.0`, `SESSION_TTL_MINUTES=30`) | 현재 어떤 모듈도 import하지 않음 |
| `schoolApi.py` | FactChat 게이트웨이로 "도구 호출 → 도구 실행 → 2차 호출" 왕복 1회 실행 | 단독 실행 스크립트 |
| `nvidiaApi.py` | NVIDIA 게이트웨이(`z-ai/glm-5.3-flash`)가 도구를 호출하는지만 확인 | 모듈 최상위에서 바로 API를 호출하므로 **import하면 안 됨** |

---

## 3. 도구별 실제 동작

### 3.1 `resolve_station(query)`

입력에서 공백을 모두 제거(`clean_q`)하고, 끝의 `역`을 뗀 값(`base_name`)을 만든 뒤 아래 순서로 처음 걸리는 규칙을 반환한다.

| 순서 | 규칙 | coverage | confidence |
|---|---|---|---|
| 1 | `clean_q`가 `ALIASES`에 있음 | SUPPORTED | 1.0 |
| 2 | `base_name`이 `ALIASES`에 있음 | SUPPORTED | 0.95 |
| 3 | `STATIONS`의 표준 역명 또는 ID와 정확히 일치 | SUPPORTED | 1.0 |
| 4 | 미지원 키워드 포함 (`판교`, `정자`, `광교`, `신분당선`, `9호선`, `봉은사`, `선정릉`, `여의도`, `KTX`, `부산`, `대전`) | OUT_OF_COVERAGE (`station_id=None`, `name`=걸린 키워드) | 0.9 |
| 5 | 부분 일치 (`base_name ⊂ 역명` 또는 `역명 ⊂ base_name`), `STATIONS` 순서상 첫 번째 | SUPPORTED | 0.8 |
| 6 | 그 외 | NOT_FOUND (`name`=원본 query) | 0.0 |

- "선릉 2호선", "시청 1호선"처럼 노선이 붙은 입력은 5번(부분 일치)으로 해석된다. 노선 정보는 결과에 반영되지 않는다.
- 커버리지는 "`STATIONS`에 등록된 역 = 지원"이다. 명세의 "역 단위 커버리지 목록"이 아니라, 4번의 키워드 블랙리스트에 없는 미등록 역은 `NOT_FOUND`가 된다.

### 3.2 `get_elevator_status(station_id)`

- `ELEVATORS`에 데이터가 있는 역은 9곳뿐이다: `gangnam`, `cityhall`, `yeoksam`, `euljiro1ga`, `sadang`, `sillim`, `sindorim`, `jamsil`, `hongik`. 나머지 역(또는 존재하지 않는 ID)은 오류 없이 `overall_status="UNKNOWN"`, `segments=[]`를 반환한다.
- `overall_status`는 **고장 구간 개수**로만 정한다: 고장 0개 → `AVAILABLE`, 전부 고장 → `UNAVAILABLE`, 그 외 → `PARTIAL`. `MAINTENANCE`도 고장으로 센다. 구간 종류(`exit`/`platform`/`transfer`)나 이동 방향은 고려하지 않는다.
- `updated_at`은 "현재 시각 − 5분", `freshness_minutes`는 항상 5인 **가짜 값**이다.
- 현재 Mock 상 고장이 있는 역: 사당(승강장), 신림(6번 출구), 신도림(3번 출구) — 셋 다 `PARTIAL`. `UNAVAILABLE` 역은 없다.

### 3.3 `get_broken_elevators(line_id=None)`

- `ELEVATORS`를 돌며 고장 구간이 하나라도 있는 역을 모은다. `line_id`가 있으면 `STATIONS[역]["lines"]`에 포함된 역만 남긴다.
- 정렬: `UNAVAILABLE` 먼저, 그다음 고장 구간 수 내림차순.
- `details`는 고장 구간의 원본 dict 목록이다 (`ElevatorSegment` 모델이 아님).

### 3.4 `plan_trip(start_id, end_id, arrive_by=None, depart_at=None, home_walk_min=7)`

**경로**

- 두 역이 모두 `LINE_2_LOOP`에 있으면 시계(외선)·반시계(내선) 중 정거장이 적은 쪽을 고른다. 같으면 시계 방향.
- 하나라도 순환선 밖이면(예: `seoul`, `jongno3ga`, `express_bus`) 경로를 `[start, end]`, **1정거장**으로 처리한다.
- `legs`는 항상 2호선 한 구간, `transfers`는 항상 0이다. 환승 경로는 계산하지 않는다.

**시간 계산 (단위: 분)**

```text
총 소요 = home_walk + station_entry_elevator + worst_case_wait + subway_ride + exit_elevator
        =    7      +           3            + (3 + 2)         + 정거장×2    +      3
```

- `worst_case_wait` = 2호선 배차 간격(3) + 안전 여유(2) = **5분**. 평균 대기(배차/2)가 아니라 보수적으로 잡는다.
- `transfer_walk`는 항상 0. 다른 노선 배차(`default_headway=5`)는 아직 쓰이지 않는다.
- `home_walk_min=None`이면 `TIMING_PARAMS["home_walk"]`(7)을 쓴다.

**모드별 결과**

| 모드 | 조건 | 계산 |
|---|---|---|
| 역산 | `arrive_by` 있음 | 도착 = 오늘 `arrive_by` (이미 지났으면 **내일**로 넘김). 출발 = 도착 − 총 소요. 승차 = 출발 + (도보 + 진입 + 대기). `minutes_from_now` = 출발 − 현재, 음수면 `is_late=True` |
| 순방향 | `arrive_by` 없음 | 출발 = 오늘 `depart_at` 또는 현재. 도착 = 출발 + 총 소요. `minutes_from_now=0`, `is_late=False` 고정 |

- `arrive_by` 형식이 잘못되면(예: `"25:00"`) 오류 없이 **조용히 순방향 모드로 넘어간다**.
- `depart_at` 형식이 잘못되면 `ValueError`가 그대로 발생한다.
- `is_possible`은 항상 `True`. 첫차/막차 시간은 반영하지 않는다.

**엘리베이터 우회**

- **도착역**이 `PARTIAL`/`UNAVAILABLE`이면 `_find_alternative`로 대체역 1곳을 찾아 `detours`와 `warnings`에 넣는다.
  1. `ALTERNATIVE_TIPS`에 `"{도착역}>{대체역}"` 키가 있으면 그 팁 사용 (거리 800m / 도보 10분 고정값).
  2. 없으면 2호선 순환선의 다음 역(시계 방향)을 대체역으로 사용 (950m / 12분 고정값).
- **출발역**이 고장이면 `warnings`에 문구만 추가하고 대체역은 찾지 않는다.
- 환승역은 검사하지 않는다. 따라서 `target_type`은 항상 `"end"`이다.
- 대체역을 찾아도 경로·시간은 다시 계산하지 않는다 (대체역까지의 도보 시간 미반영).

---

## 4. 폴백 엔진 (`FallbackEngine.process`) 동작

### 4.1 처리 순서

1. `extract_stations`: 메시지를 `[가-힣0-9a-zA-Z_]+` 토큰으로 나눠 토큰마다 `resolve_station`을 호출하고, `SUPPORTED`인 역만 순서대로(중복 제거) 모은다.
2. `parse_korean_time`: 시각 표현을 `HH:MM`으로 변환.
3. `classify_intent`: 아래 점수표로 의도 결정.
4. 문맥 보정: 역이 하나도 없고 "거기/그 역/여기…" 같은 지칭어가 있으면 `session_ctx["last_station_id"]`를 역으로 사용. **의도 분류는 이 보정 전에 끝난다.**
5. 의도별 핸들러가 도구를 호출하고 `ChatResponse`(`engine="fallback"`, `fallback_used=True`)를 조립.

`session_ctx`는 읽기만 한다. 응답 후 세션 갱신은 호출하는 쪽(향후 오케스트레이터) 책임이다.

### 4.2 의도 분류 점수표

| 조건 | 가점 |
|---|---|
| 강한 출발 표현 (`출발`, `몇 시에`, `나가야`, `늦지 않게` 등) | departure +4 |
| 약한 출발 표현 (`까지 가려면`, `도착하려면` 등) | 시각 있으면 departure +4, 없으면 route +3 |
| 시각 있음 + (`까지`/`도착`/`가려면`/`에 가`) | departure +3 |
| 대체/고장 표현 (`고장`, `안 돼`, `우회`, `다른 역`, `어디로` 등) | alternative +3 |
| `대체역` 또는 `우회` 포함 | alternative +3 (추가) |
| 고장 목록 표현 (`고장 난 역`, `현황`, `목록` 등) | 역 언급이 없을 때만 outages +6 |
| 경로 표현 (`가는 길`, `경로`, `환승`, `가려면` 등) | route +3 |
| 역 2개 이상 | route +1 |
| 시각 있음 | route −2 |
| 엘리베이터 표현 (`엘베`, `휠체어`, `상태`, `탈 수` 등) | elevator +2 |
| 인사 표현 | 역 언급이 없을 때만 greeting +2 |
| outages 점수 > 0 | alternative −2 |

최고점이 0 이하이면 `elevator`로 처리한다.

### 4.3 시각 파싱 규칙

- `14:45`처럼 `HH:MM`이 있으면 그대로 사용.
- `정오` → 12:00, `자정` → 00:00.
- `N시`, `N시 M분`, `N시 반` (N은 숫자 또는 `한`~`열두`).
- `오후/저녁/밤` → 12 미만이면 +12. `오전/아침/새벽` + 12시 → 0시.
- **오전/오후 표시가 없고 1~6시면 오후로 간주** (예: "3시 반" → 15:30). 7시 이상은 그대로 (예: "7시" → 07:00).

### 4.4 의도별 응답

| 의도 | 호출 도구 | card_type | 액션 |
|---|---|---|---|
| greeting (역 없음) | 없음 | `help` | 없음 |
| outages | `get_broken_elevators()` (노선 필터 없음) | `outages_list` / `outages_empty` | 1순위 역 `openStation` |
| departure / route | `plan_trip` | `trip_plan` | `openTime` (`originId`, `destId`, `arriveBy`) |
| elevator / alternative | `get_elevator_status`, 고장이면 `plan_trip(sid, sid)`로 대체역 조회 | `elevator_status` | `openStation`, 대체역 있으면 `showAlternative`를 맨 앞에 추가 |

- departure/route의 출발·도착역: 역 2개 → 첫 번째가 출발, 두 번째가 도착. 역 1개 → 도착역, 출발은 `last_station_id` 또는 기본값 `sindorim`. 역 0개 → 신도림→신도림.
- elevator/alternative에서 역이 없으면 **강남역**을 기본 대상으로 한다.
- `metadata.latency_ms`는 항상 0이다.

---

## 5. 명세와 현재 구현의 차이

| 항목 | 명세 / 설계 검토 | 현재 구현 |
|---|---|---|
| LLM 공급자 | OpenRouter / Gemini (명세 §9) | 학교 FactChat 게이트웨이 + NVIDIA 게이트웨이 실험 |
| LLM 타임아웃 | 2.5초 (명세 §1, §6.2, CARD-004) | `config.py` 기본값 3.0초 — **팀 확정 필요** |
| `worst_case_wait` | 예시: 배차 3 + 안전 여유 1 = 4분 (명세 §3.4) | 배차 3 + 안전 여유 2 = 5분 |
| 커버리지 판정 | 역 단위 목록으로 관리 (설계 검토 §2.2) | `STATIONS` 등록 여부 + 키워드 블랙리스트 |
| 엘리베이터 이용 가능 판정 | 이동 방향에 필요한 구간 기준 (설계 검토 §2.3) | 고장 구간 개수 기준 |
| 고장 우회 | 출발/환승/도착 모두, 그래프에서 고장 노드 제외 (설계 검토 §2.3) | 도착역만, 고정 팁 또는 다음 역 |
| 경로 탐색 | 다익스트라, 환승 포함 | 2호선 순환선 방향 비교만 |
| 오전/오후 모호성 | 현재 시각 이후 가장 가까운 시각 (설계 검토 §2.1) | 1~6시는 오후 고정, 지난 시각은 다음 날 |
| 첫차/막차 | 범위 밖이면 예외 응답 (설계 검토 §2.4) | 미반영, `is_possible` 항상 True |
| 데이터 신선도 | 실제 갱신 시각 | 현재 − 5분 고정값 |
| `schoolApi.py`의 도구 정의 | 4종, `plan_trip`은 `depart_at`·`home_walk_min` 포함 | 3종 (`get_broken_elevators` 없음), `plan_trip`은 `start_id`/`end_id`/`arrive_by`만 |
| `.env` 사용 | `.env.example` → `.env` 복사 | `.env`를 읽는 코드 없음 (셸 환경 변수만 읽음) |

---

## 6. 알려진 이슈

코드 수정이 필요한 항목이다. 우선순위는 사용자에게 잘못된 안내가 나가는지를 기준으로 매겼다.

| # | 우선순위 | 이슈 | 재현 | 원인 |
|---|---|---|---|---|
| 1 | 높음 | 대체역 이름이 한글 대신 ID로 노출 ("대체역(mullae역)") | `plan_trip("gangnam", "sindorim")` — 사당·신도림·신림 3곳 모두 해당 | 대체역 `bangbae`, `mullae`, `sindaebang`이 `STATIONS`에 없어 이름 조회가 ID로 대체됨 |
| 2 | 높음 | 폴백에서 미지원 역을 물으면 강남역 정보로 답함 | `FallbackEngine.process("판교역 엘리베이터")` → "강남역의 모든 엘리베이터는 정상…" | `extract_stations`가 `OUT_OF_COVERAGE`를 버리고, 역이 없으면 강남을 기본값으로 씀 |
| 3 | 중간 | 부분 일치가 너무 느슨함 | `"서울"` → 서울대입구, `"신"` → 신당, `"강"` → 강남 | `resolve_station` 5단계가 `STATIONS` 순서상 첫 부분 일치를 반환 |
| 4 | 중간 | `depart_at` 형식 오류 시 예외, `arrive_by` 형식 오류는 조용히 무시 | `plan_trip(..., depart_at="abc")` → `ValueError` | 두 모드의 입력 검증 방식이 다름 |
| 5 | 낮음 | 폴백 `tool_calls` 기록이 실제 호출과 다름 | departure 응답의 `resolve_station` args가 `{"query": "jamsil"}` (역 ID) | 기록용 `ToolCallRecord`를 별도로 만들면서 ID를 넣음 |
| 6 | 낮음 | `서울역`만 표준 역명에 "역"이 붙어 있어 문장에 "서울역역"이 나옴 | 서울역 관련 폴백 응답 | `STATIONS["seoul"]["name"] = "서울역"` |
| 7 | 낮음 | `nvidiaApi.py`를 import하면 즉시 API 호출 | `import AI.src.nvidiaApi` | 호출 코드가 `if __name__ == "__main__":` 밖에 있음 |
| 8 | 낮음 | 모델 기본값 불일치 | `schoolApi.py`는 `gpt-5.1`, `config.py`·`.env.example`은 `claude-sonnet-5` | `schoolApi.py`가 `config.py`를 쓰지 않고 자체 기본값을 가짐 |

---

## 7. 개발 환경 메모

- 테스트와 스크립트는 `AI/`의 **부모 폴더**에서 실행한다 (`python -m pytest AI/tests`). 모든 import가 `AI.src.*` 절대 경로이기 때문이다. `schoolApi.py`는 스스로 부모 폴더를 `sys.path`에 추가하므로 어디서 실행해도 된다.
- Windows 콘솔(cp949)에서 이모지를 출력하는 스크립트는 `PYTHONIOENCODING=utf-8` 또는 `python -X utf8`로 실행한다.
- 이 레포의 `README.md`는 UTF-8로 저장한다 (이전에는 UTF-16이라 git이 바이너리로 취급해 diff가 보이지 않았다).
