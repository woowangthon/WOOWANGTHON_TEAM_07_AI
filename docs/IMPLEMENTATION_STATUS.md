# AI 파트 구현 현황 및 동작 상세 (Implementation Status)

> 이 문서는 **현재 코드가 실제로 어떻게 동작하는지**를 정리한다. 목표 설계는 [`AI_DEVELOPMENT_SPEC.md`](./AI_DEVELOPMENT_SPEC.md)에 있다.
> 코드 동작을 바꾸면 이 문서의 해당 절도 같이 고친다.
>
> 최종 갱신: 2026-11-15 · 단위 테스트 324건 통과 (skip 없음)

---

## 1. 구현 상태

| 구성 요소 | 명세 | 상태 | 파일 |
|---|---|---|---|
| 도구 4종 입출력 스키마 | §3 | ✅ | `src/models.py` |
| 도구 4종 로직 | §3 | ✅ Mock·BE 데이터 소스. 상태 판정은 BE 계약 §6 규칙 | `src/tools/station.py`, `elevator.py`, `trip.py`, `src/data/mock_data.py` |
| LLM용 도구 정의 + 실행기 | §3 | ✅ | `src/tools/registry.py` |
| 시스템 프롬프트 | §5.1 | ✅ 템플릿 | `src/llm/prompts.py` |
| 규칙 기반 폴백 엔진 | §6.2 | ✅ | `src/fallback/engine.py` |
| 환경 설정 (`.env` 자동 로딩) | — | ✅ | `src/config.py` |
| LLM 게이트웨이 왕복 데모 | §9 Phase 2 | ✅ 실험용 | `scripts/llm_demo.py` |
| `/api/chat` 요청/응답 모델 | §4 | ✅ | `src/models.py` |
| 데이터 소스 인터페이스 (Mock/BE 교체) | — | ✅ Mock, BE API(`AI_DATA_SOURCE=be`). BE 데모 서버로 실제 연동 확인 (§4.8) | `src/data/source.py`, `mock_source.py`, `be_source.py` |
| FastAPI `/api/chat` 엔드포인트 (오케스트레이터) | §4 | ✅ FactChat 50문항 왕복 확인. 기본 설정은 `gemini-3.5-flash-lite` + 응답 속도 옵션 둘 다 켬(P95 약 1.5~1.8초, 목표 안. 팀 결정 2026-11-14, §4.6) ([BENCHMARK](./BENCHMARK.md) §4) | `src/api/app.py`, `src/llm/orchestrator.py`, `client.py` |
| 액션·카드 조립 (도구 결과 기반) | §4 | ✅ | `src/llm/response_builder.py` |
| 세션 슬롯 메모리 (TTL 30분) | §5.2 | ✅ 인메모리 | `src/llm/session.py` |
| LLM 장애 → 폴백 전환 | §6.2 | ✅ | `src/llm/orchestrator.py` |
| 후처리 환각 검증기 (Guard) | §6.1 | ✅ 역명·시각만 | `src/llm/guard.py` |
| 50문항 벤치마크 | §8 | ✅ 폴백·`gpt-5.1` 측정, 프롬프트 1차 튜닝 ([BENCHMARK](./BENCHMARK.md)) | `src/eval/`, `scripts/run_benchmark.py` |
| 백엔드 실데이터 연동 | §7 | 🔸 역·엘리베이터·대체역·경로(`GET /api/routes`)·출발 시각(`POST /api/departure-time`)을 BE API로 연동 (이슈 #6). 경로·출발 시각 테스트는 실제 BE(main 5ff20c1, dev.db 복사본) 녹화 응답을 쓴다(이슈 #12). 운행 시간 밖 판정 없음(§6) ([DATA_SPEC](./DATA_SPEC.md) §9) | `src/data/be_source.py` |
| 데이터 미확인·BE 장애 안내 | — | ✅ `limitations` 전달·강제, BE 장애 시 "확인할 수 없음" (§4.9) | `src/tools/elevator.py`, `src/llm/orchestrator.py` |

---

## 2. 도구별 실제 동작

LLM은 `tools.registry.execute_tool(name, args, now=None)`로 도구를 실행한다. 인자를 `*Input` 모델로 검증하고, 실패하거나 도구가 `ValueError`를 내면 예외 대신 `{"error": "..."}`를 돌려준다. `now`는 `plan_trip`에만 전달되며 오케스트레이터가 요청의 `current_time`으로 채운다.

도구와 폴백 엔진은 `mock_data.py`를 직접 읽지 않고 데이터 소스 인터페이스(`data.get_data_source()`)를 거친다 (§4.8). 아래 동작은 기본 데이터 소스인 `MockDataSource` 기준이며, BE 데이터 소스와 다른 점은 §4.8에 적는다. 데이터 소스(BE)에 연결할 수 없으면 도구는 `DataUnavailableError`를 내고, 실행기는 `{"error", "data_unavailable": true}`를 돌려준다 (§4.9).

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

상태 판정은 BE 계약(`BE/docs/api-contract.md` §6.2)과 같다 (`tools/elevator.judge`). T = 엘리베이터 수, U = 상태 미확인(`UNKNOWN`) 수.

| 상황 | `overall_status` | `data_fresh` | `limitations` |
|---|---|---|---|
| 전부 확인, 전부 가동 / 섞임 / 전부 불가 | `AVAILABLE` / `PARTIAL` / `UNAVAILABLE` | true | 없음 |
| 일부 미확인 (0 < U < T) | 확인된 설비로 위와 같이 판정 | false | "일부 설비 상태가 확인되지 않아 역 전체 상태를 단정할 수 없음" |
| 전부 미확인 (U = T > 0) | `UNKNOWN` | false | "확인 가능한 엘리베이터 상태가 없어 역 상태를 판정할 수 없음" |
| 엘리베이터 정보 없음 (T = 0) | `UNKNOWN` | false | "엘리베이터 정보가 없어 역 상태를 판정할 수 없음" |

- 미확인 설비를 정상으로 채우지 않는다. 설비 상태는 `AVAILABLE` / `UNAVAILABLE` / `UNKNOWN`(+ `unknown_reason` `NO_DATA`/`STALE`). 예전의 `MAINTENANCE`는 없앴다(BE는 보수중을 `가동 안됨`으로 본다).
- 역 자체의 한계 안내(BE: 1호선 역의 "1호선은 일부 구간의 역만 포함되어 있어 …")도 `limitations`에 붙인다.
- `updated_at`은 설비 `fetched_at` 중 가장 최근 값. 설비에 수집 시각이 없으면(Mock) "현재 − 5분", 엘리베이터가 없으면 `None`.
- 엘리베이터 데이터가 있는 Mock 역은 9곳(`ELEVATORS`). 나머지 지원 역은 `UNKNOWN`(엘리베이터 정보 없음). 지원 역 목록에 없는 ID는 `ValueError` — 범위 밖 역("판교")을 지원 역처럼 안내하지 않기 위해서다.
- 현재 Mock 상 고장 역: 사당(2호선 승강장), 신림(6번 출구), 신도림(3번 출구) — 모두 `PARTIAL`, 전부 확인됨.
- 안내 문장은 `describe_status`(응답 빌더·폴백 공용). `data_fresh=false`면 "○대 중 상태가 확인된 ○대는 가동 중 … 단정할 수 없습니다"처럼 확인된 범위만 말한다. `UNKNOWN`이면 "상태를 확인할 수 없습니다. 이용 가능하다고 판단하지 마시고 …".

### 2.3 `get_broken_elevators(line_id=None)`

- 이용 불가가 **확인된** 설비가 있는 역만 모은다(§2.2 판정이 `PARTIAL`/`UNAVAILABLE`). 미확인 설비는 고장으로 세지 않는다. `line_id`가 있으면 그 노선이 지나는 역만.
- 미확인 설비가 하나라도 있으면 `limitations`에 "일부 설비의 상태 정보가 없거나 오래되어 현재 상태를 확인하지 못함"(목록이 전부가 아닐 수 있음).
- 정렬: `UNAVAILABLE` 먼저, 그다음 불가 설비 수 내림차순. `details`는 불가 설비 목록, `unknown_count`는 역별 미확인 수.

### 2.4 `plan_trip(start_id, end_id, arrive_by=None, depart_at=None, home_walk_min=7, *, now=None)`

**입력 검증**: 모르는 역 ID, `HH:MM`이 아닌 시각은 `ValueError`. `now`는 LLM에 노출되지 않는 인자로, 테스트나 요청의 `current_time`을 넣을 때 쓴다.

**경로 (Mock, `route_source="AI"`)**

- 두 역이 모두 2호선 순환선(`LINE_2_LOOP`, 43역)에 있으면 외선·내선 중 짧은 쪽. 같으면 외선(시계 방향).
- 하나라도 순환선 밖(서울역, 종로3가, 고속터미널)이면 `is_possible=False`, 경로 `[start, end]`, 정거장 0, `warnings[0]`에 사유. 이때 시간 값은 의미가 없다.
- 환승은 계산하지 않는다 (`transfers=0`, leg 1개).

**경로 (BE, `AI_DATA_SOURCE=be`, 이슈 #6, `route_source="BE"`)**: 출발역≠도착역이면 BE `GET /api/routes?from=&to=`(계약 §8.4)를 부른다.

- `route.path` = `stations[].station_id`(문자열), `legs` = 타임라인 `RIDE` 구간마다 하나(`line_id`, 시작·끝 역, `stops`, `ride_minutes`), `transfers` = `transfer_count`, `transfer_station_ids` = `TRANSFER` 구간의 역, `total_stops` = `RIDE` `stops` 합, `total_duration_minutes` = BE `total_minutes`(역 간 2분·환승 5분 가정의 추정값). `breakdown.subway_ride` = `RIDE` 합, `breakdown.transfer_walk` = `TRANSFER` 합.
- BE가 404(두 역을 잇는 경로 없음)면 `is_possible=False`, `warnings[0]` = "○○역→○○역은 두 역을 잇는 경로를 찾지 못해 경로·출발 시각을 안내할 수 없습니다.", 출발 시각 API는 부르지 않는다. 경로를 지어내지 않는다. (역 존재는 먼저 확인하므로 404는 경로 없음이다.)
- 응답 모양이 계약과 다르면(필드 누락, `total_minutes` ≠ 타임라인 합, 계약 밖 `status`/`kind`) `DataUnavailableError`. 5xx·연결 실패도 같다.
- 엘리베이터 경고: `elevator_warning`(`status` WARNING/UNCONFIRMED/CLEAR, `items[]` 객체: `station_id`·`station_name`·`kind`·`message`)을 그대로 `PlanTripOutput.elevator_warning`에 담는다(역 ID는 문자열, 역명은 기본 이름). 출발·도착역은 아래 우회 단계에서 직접 확인하므로 빼고, 나머지 역 중 `UNAVAILABLE`/`PARTIAL`은 `warnings`에 "경로 위 경유역|환승역 ○○역: (BE 문구)."로, `UNCONFIRMED`인 **환승역**은 `limitations`에 "환승역인 ○○역 엘리베이터 상태를 확인하지 못함"으로 넣는다. 경유역 미확인은 개별로 적지 않고, `status`가 CLEAR가 아니면 BE `message`를 `limitations`에 넣는다(BE 출발 시각 API와 같은 규칙. CLEAR는 "확인된 설비 기준"이라 아무것도 덧붙이지 않는다).
- BE 경로 `limitations`(추정 소요 시간, 시간표 추출 간선, 접근성 미검증, 1호선 등)는 문구를 바꾸지 않고 그대로 전한다.
- 출발=도착(대체역만 묻기)은 경로 API를 부르지 않는다(BE는 같은 역을 422로 거절).

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

BE 경로면 위 식의 "정거장 수 × 2"는 BE `total_minutes`(열차 이동 + 환승)다.

**BE 역산 (`AI_DATA_SOURCE=be`, 이슈 #6)**: 역산 모드이고 `is_possible=True`, 출발역≠도착역이면 출발 시각을 BE `POST /api/departure-time`(계약 §8.2)으로 계산한다 (`time_source="BE"`). 그 밖(순방향·지금 출발, 대체역만 묻기, 경로 없음)과 Mock 데이터 소스는 위 계산을 그대로 쓴다 (`time_source="AI"`). BE에는 순방향 출발 시각 API가 없어 순방향은 BE 경로 소요 시간으로 AI가 더한다.

- 보내는 값: `arrival_at` = 위 규칙으로 정한 도착 날짜·시각 + `+09:00`, `travel_minutes` = BE 경로 `total_minutes`(BE 결정 D17), `from_station_id`·`to_station_id` = 출발·도착역 ID(정수), `buffer_minutes` = 최악 대기 5분(배차 3 + 여유 2), `walk_to_station_minutes` = `home_walk_min`(기본 7), `station_entry_minutes` 3, `exit_walk_minutes` 3. 분 값은 BE 기본값(D15 이후 도보 7·진입 3·출구 3·대기 5)과 같지만 명시해서 보낸다 — 사용자가 도보 시간을 바꿀 수 있고, BE 기본값이 바뀌어도 AI 안내와 어긋나지 않게 하려는 것이다 (`tests/data/test_be_source.py::test_ai_time_constants_match_be_departure_defaults`).
- 역 ID를 보내므로 BE가 경로 위 엘리베이터 경고를 계산하고, 환승 없는 경로이고 시간표에 맞는 열차가 있으면 그 열차의 계획 시각으로 탑승·하차를 맞춘다. 이때 `timetable_used=true`, `selected_train` = `train_no`·계획 탑승/하차 `HH:MM`·`direction_inferred`, `breakdown.subway_ride` = BE 타임라인 `RIDE` 분(입력 이동 시간은 쓰지 않음), `transfer_walk` = 0. `selected_train`이 `null`이면 `None`. 형식이 다르면(시각 없음 등) `DataUnavailableError`.
- `departure_time`·`board_time`·`arrival_time`은 BE의 `recommended_departure_at`·`estimated_boarding_at`·`arrival_at`을 KST `HH:MM`으로 옮긴 값이다. `minutes_from_now`·`is_late`는 AI 기준 시각(`now`)으로 계산한다. BE `already_past`는 BE 서버 시계 기준이라 쓰지 않는다.
- `limitations` 순서: BE 출발 시각 문구 → BE 경로 문구 → AI 항목(역 상태 미확인 등), 중복 제거. BE 문구는 바꾸지 않고 그대로 전한다. BE 출발 시각 문구는 `timetable_used`를 따른다 — `false`면 시간표 미사용·이동 시간 외부 입력·도보·진입·출구 추정 + 이유("시간표에서 … 맞는 열차를 찾지 못해 추정 계산을 사용함" 또는 "환승이 필요한 경로는 …"), `true`면 시간표 계획 시각·방향 추정 등(BE 계약 §8.2, R14). 그 뒤의 경로 엘리베이터 경고 문구(`elevator_warning.message`)도 그대로 전한다. 빼는 것은 두 가지뿐이다: "역·설비 상태 정보가 입력되지 않아 …"(역 ID를 안 보냈을 때만 붙음)와 "추천 출발 시각이 현재 시각보다 이미 지났음"(BE 서버 시계 기준. 요청의 `current_time`과 다를 수 있어 `is_late`로 대신 알림).
- 순방향(BE 경로): `limitations` 앞에 "실제 열차 시간표를 사용하지 않은 추정 계산이며 탑승 가능한 열차는 확인되지 않음", "도보·역 진입·출구 이동 시간은 추정값이며 개인과 상황에 따라 다를 수 있음"(BE와 같은 문장) + BE 경로 문구.
- BE 연결 실패·타임아웃·200 외 응답(404·422 포함)·해석 실패 → `DataUnavailableError`. 직접 계산으로 대신 답하지 않는다.
- **운행 시간 밖(첫차 전·막차 후)은 판정하지 않는다.** BE 응답에 이를 알리는 값이 없다. "맞는 열차를 찾지 못해" 문구는 시간표가 없는 데모 DB에서는 모든 한 노선 경로에 붙으므로(시간표 없음·방향 불명과 구분 불가) 이 문구로 `is_possible=False`를 만들지 않고 그대로 전한다 (§6).

**엘리베이터 우회**

- 출발역과 도착역(같은 역이면 한 번)의 상태가 `PARTIAL`/`UNAVAILABLE`이면 `find_alternatives`로 대체역을 찾아 `detours`(`target_type` = `start`/`end`)와 `warnings`에 넣는다. 출발=도착이면 후보를 모두, 경로면 첫 후보만 넣는다.
  - 데이터 소스가 대체역을 계산하면(BE `/alternatives`) 그 결과와 `limitations`를 그대로 쓴다.
  - Mock: (1) `ALTERNATIVES`에 등록된 안내. 대체역이 고장으로 확인되면 제외하고, 상태가 미확인이면 `limitations`에 "대체역 ○○역의 엘리베이터 상태는 확인되지 않음". (2) 없으면 순환선 양옆 역 중 가동이 **확인된** 역(`AVAILABLE`/`PARTIAL`).
  - 거리·도보 시간은 확인된 값만 넣는다(없으면 `None`). 예전의 기본값 800m / 10분은 없앴다.
- 상태가 `UNKNOWN`인 역에는 대체역을 붙이지 않고 `limitations`에 "○○역 엘리베이터 상태를 확인하지 못함"을 넣는다. 일부만 확인된 역은 "○○역: 일부 설비 상태가 …".
- 경로를 계산했으면 `estimated=true`, `limitations` 첫 줄에 "열차 시간표와 실제 경로 탐색을 쓰지 않은 추정 계산이며 탑승 가능한 열차는 확인되지 않음" (Mock 경로로 AI가 직접 계산한 경우에만. BE 역산을 쓴 경우는 위의 BE 문구이며 `timetable_used`에 따라 달라진다). `estimated`는 BE가 시간표로 열차를 골라도 계획 시각이라 항상 `true`다.
- 경유·환승역은 BE 경로의 엘리베이터 경고로만 알린다(위 BE 경로 참고). 대체역은 출발·도착역에만 붙이고, 대체역을 찾아도 경로·시간은 다시 계산하지 않는다.
- 응답 문장: 순방향 요약에 BE 경로의 환승 횟수를 "(열차 N개 역, 환승 M회, …)"로 넣는다. LLM 경로에서도 `plan_trip`의 `warnings`가 LLM 문장에 없으면 "⚠️ …"로 덧붙인다(`limitations`의 "※ …"와 같은 방식, §4.2).
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
| `outages_list` / `outages_empty` | 고장 목록 의도 | `get_broken_elevators` (문장에 `N호선`이 있으면 `line_id=N`) | 상위 3역 `openStation` |
| `out_of_coverage` | 미지원 지역/노선 언급 | (`resolve_station` 기록만) | — |
| `need_station` | 필요한 역을 알 수 없음, 사전에 없는 역, 출발=도착 | — | — |
| `trip_plan` | departure/route | `resolve_station`, `plan_trip` | `openTime`, 대체역마다 `showAlternative` |
| `trip_unsupported` | `plan_trip`이 `is_possible=False` | 〃 | — |
| `data_unavailable` | 데이터 소스(BE) 장애 (오케스트레이터가 만듦, §4.9) | — | — |
| `elevator_status` | elevator/alternative | `resolve_station`, `get_elevator_status`, 고장이면 `plan_trip(sid, sid)` | 대체역 있으면 `showAlternative`, `openStation` |

- departure/route의 출발·도착: 역 2개 → 앞이 출발. 역 1개 → 도착역, 출발은 `last_station_id` 또는 신도림(이 경우 "출발역이 지정되지 않아 신도림역 기준으로 안내합니다" 문구 포함).
  - 단, 역 1개와 함께 지칭어가 도착지로 쓰이면("신촌에서 **그 역까지**", "거기로") 말한 역이 출발, `last_station_id`가 도착이다.
- `tool_calls`에는 실제로 호출한 도구와 인자를 그대로 기록한다.
- 도구 결과의 `limitations`는 문장 끝에 "※ …" 줄로 붙인다 (응답 빌더와 같음). 기본 출발역은 역 이름("신도림")으로 두고 `resolve_station`으로 ID를 찾는다.
- 데이터 소스 장애면 `DataUnavailableError`를 그대로 올린다. 오케스트레이터가 `data_unavailable`로 답한다.
- `metadata.latency_ms`는 0 (측정은 오케스트레이터 몫).

---

## 4. 오케스트레이터 (`POST /api/chat`)

### 4.1 처리 흐름 (`ChatOrchestrator.handle`)

1. `session_id`로 세션 슬롯을 꺼내 시스템 프롬프트(`build_system_prompt`)에 최근 역·최근 경로를 넣는다.
2. LLM에 `TOOL_SPECS`와 함께 보낸다. 응답에 도구 호출이 있으면 `execute_tool`로 실행하고 결과를 `tool` 메시지로 붙여 다시 보낸다. 최대 `LLM_MAX_TOOL_ROUNDS`(기본 4)회.
3. 도구 호출 없이 문장이 오면 끝. `card_type`·`actions`·`suggestions`는 도구 결과로 코드가 만든다 (§4.2).
   - **대체역 보충**: 엘리베이터 이용 불가 구간이 있는 역(`PARTIAL`/`UNAVAILABLE`)을 조회했는데 LLM이 그 역의 대체역(`plan_trip(sid, sid)`)을 부르지 않았으면, 코드가 직접 조회해 `tool_calls`에 추가한다. 경로 질문(`plan_trip`, 출발≠도착)이 있으면 그 결과에 대체역이 이미 있으므로 보충하지 않는다.
   - 보충한 대체역이 LLM 문장에 없으면 "💡 추천 대체역: ○○역 (안내 문구)" 한 줄을 도구 결과로 덧붙인다.
   - **limitations 강제**: 도구 결과의 `limitations` 문장이 LLM 문장에 그대로 없으면 "※ …" 줄로 덧붙인다 (BE 계약 R9. LLM이 "확인되지 않음"을 빼고 요약하지 못하게).
4. 후처리 검증(§4.5)에 걸리면 LLM 문장을 버리고 도구 결과로 만든 템플릿 문장을 쓴다. 검증 근거는 LLM이 실제로 본 도구 결과뿐이며, 보충 조회 결과는 템플릿에만 쓴다.
5. 세션 슬롯을 갱신하고(§4.4) `metadata.latency_ms`를 기록한다.

- 대화 원문은 저장하지 않는다. 요청마다 시스템 프롬프트 + 이번 사용자 문장으로 새로 시작한다.
- 도구를 부른 assistant 메시지는 게이트웨이가 준 원본 그대로 대화에 다시 넣는다 (모델별 추가 필드 보존, 예: Gemini `thought_signature`).
- `/api/chat`은 동기 함수이며 FastAPI 스레드 풀에서 실행된다. 명세의 `async/await`는 아직 쓰지 않는다.
- 실행: `python scripts/run_server.py` (기본 포트 8001). LLM 키가 없으면 모든 요청이 폴백으로 답한다.

### 4.2 카드·액션 조립 (`response_builder.build_response`)

도구 결과에서 아래 순서로 처음 해당하는 것을 고른다. `card_type` 이름은 폴백 엔진(§3.4)과 같다. 오류 결과(`{"error"}`)는 건너뛴다. 단, 데이터 소스 장애 결과(`data_unavailable`)가 하나라도 있으면 다른 결과와 상관없이 `data_unavailable`이다(§4.9). 템플릿 문장 끝에는 결과의 `limitations`를 "※ …" 줄로 붙인다. `trip_plan`은 `plan_trip`의 `warnings`도 "⚠️ …" 줄로 넣고, LLM 문장에 빠진 경고는 오케스트레이터가 "⚠️ …"로 덧붙인다(`BuiltResponse.warnings`, 이슈 #6 — 경유·환승역 고장을 LLM이 빼지 못하게).

| 순서 | 근거 | card_type | 액션 |
|---|---|---|---|
| 1 | `plan_trip` (출발≠도착) | `trip_plan` / `trip_unsupported` | `openTime`, 대체역마다 `showAlternative` |
| 2 | `get_elevator_status` | `elevator_status` | 같은 역 `plan_trip(sid, sid)`에 대체역이 있으면 `showAlternative`, 그리고 `openStation` |
| 3 | `plan_trip` (출발=도착, 대체역만) | `elevator_status` | `showAlternative`, `openStation` |
| 4 | `get_broken_elevators` | `outages_list` / `outages_empty` | 상위 3역 `openStation` |
| 5 | `resolve_station` OUT_OF_COVERAGE | `out_of_coverage` | — |
| 6 | `resolve_station` NOT_FOUND | `need_station` | — |
| 7 | 도구 호출 없음 | `help` | — |

`metadata.data_updated_at`은 고른 결과의 `updated_at`(데이터 수집 시각)이다. `elevator_status`(`get_elevator_status`)와 `outages_list`·`outages_empty`(`get_broken_elevators`)만 값이 있고, 수집 시각이 없는 결과(엘리베이터 데이터가 없는 역, `trip_plan`·`trip_unsupported`, 대체역만 답한 응답, `out_of_coverage`, `need_station`, `help`, `data_unavailable`)는 `None`이다. 응답 생성 시각으로 채우지 않는다. 폴백 엔진도 같은 규칙이다 (`tests/llm/test_metadata_updated_at.py`).

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
| `guard_rejected` | 문장이 후처리 검증(§4.5)에 걸렸는데 도구 결과로 만들 템플릿이 도움말뿐임 (도구를 안 불렀거나 `resolve_station`만 부름) |
| `internal_error` | 도구 실행·응답 조립 중 예기치 못한 예외 |
| `data_unavailable` | 데이터 소스(BE) 장애. 폴백 엔진도 같은 소스를 쓰므로 폴백하지 않고 바로 "확인할 수 없음"으로 답한다 (§4.9) |

- 타임아웃은 **호출 1회 기준**이다. 도구 왕복이 여러 번이면 전체 응답 시간은 그보다 길어질 수 있다.

### 4.4 세션 슬롯 (`session.py`)

- `SessionContext`: `last_station_id`, `last_station_name`, `last_route`(`start_id`, `end_id`, `arrive_by`, 출발·도착 시각), `last_query_type`(= card_type), `updated_at`.
- `SessionStore`: 인메모리. 마지막 저장 후 `SESSION_TTL_MINUTES`(30분)가 지나면 버린다. 저장할 때마다 만료 세션을 지우고, `max_sessions`(10,000)를 넘으면 오래된 것부터 버린다. 서버를 재시작하면 사라진다.
- 최근 역 우선순위: 경로의 도착역 > 상태를 조회한 역 > 대체역을 찾은 역 > `resolve_station`으로 확인된 역. 데이터 소스에 없는 역 ID는 무시한다.
- 폴백 응답도 `tool_calls` 인자로 같은 규칙을 적용해 갱신한다. 그래서 LLM이 실패한 다음 요청에서도 "거기"가 이어진다.

### 4.5 후처리 검증 (`guard.check_text`)

LLM 문장에서 `○○역`(앞 2~10글자)과 시각(`HH:MM`, `N시 M분`, `N시 반`, `N시`)을 뽑아, 이번 요청의 근거에 있는지 확인한다.

- 근거: 도구 인자·결과의 모든 문자열(역 ID는 역명으로 바꿈), 사용자 문장, 기준 시각.
- 역명: 근거의 역명 중 하나로 **끝나면** 통과 ("인접한강남역"처럼 앞말이 붙어 잡힌 경우). "대체역", "해당역", "정차역", "열차역" 같은 일반 표현과 "지역", "구역"처럼 '역'으로 끝나는 명사는 검사하지 않는다.
- 시각: 근거 시각과 분이 같고 시가 12시간 차이 이내로 같으면 통과 ("3시" = 15:00).
- 하나라도 걸리면 `metadata.guard_triggered=true`, 문장은 §4.2의 템플릿 문장으로 바꾼다. 카드·액션은 그대로다.
- 도구 결과로 만들 템플릿이 도움말(`help`)뿐이면 대신 폴백 엔진으로 답한다(`fallback_reason="guard_rejected"`). 벤치마크에서 LLM이 "거기 대체역은?"에 도구 없이 역명을 지어낸 경우가 여기에 해당했다.
- 검사하지 않는 것: '역' 없이 쓴 역명, 소요 시간·거리 숫자, 엘리베이터 위치 설명.

### 4.6 응답 속도 옵션 (이슈 #17)

`gpt-5.1`은 호출 1회에 약 1.2~3.3초가 걸리고 한 질문에 2~3회 호출해 P50이 약 5.5초다. 호출 횟수를 줄이는 두 옵션을 두었다. **기본값은 둘 다 켬 + 모델 `gemini-3.5-flash-lite`** (팀 결정 2026-11-14, 측정 [BENCHMARK](./BENCHMARK.md) §4). 예전 동작으로 되돌리려면 `.env`에 `FACTCHAT_MODEL=gpt-5.1`, `LLM_PRERESOLVE=false`, `LLM_ANSWER_MODE=llm`. `.env`에 값이 있으면 기본값보다 우선한다.

| 설정 | 값 | 동작 | 줄어드는 호출 |
|---|---|---|---|
| `LLM_PRERESOLVE` | `false`(기본) / `true` | 폴백 엔진의 역 추출로 문장 속 역을 찾아 코드가 `resolve_station`을 먼저 실행하고, 결과(ID·범위 밖·없음)를 시스템 프롬프트 끝 `[이번 문장에서 미리 확인한 역]`에 넣는다. 실행 기록은 `tool_calls`에 남는다 | 역 확인 1회 |
| `LLM_ANSWER_MODE` | `llm`(기본) / `template` | `template`이면 데이터 도구(`get_elevator_status`, `get_broken_elevators`, `plan_trip`)를 실행한 직후 LLM에 다시 묻지 않고 §4.2 템플릿 문장으로 답한다. 역 확인만 한 라운드는 계속 진행한다. LLM이 도구 없이 문장으로 답하면 그 문장을 검증해 쓴다 | 답변 문장 1회 |

- 템플릿 모드에서 사용자가 말하지 않은 출발역으로 경로를 계산했으면 문장 앞에 밝힌다: 세션 최근 역이면 "최근 조회한 ○○역에서 출발하는 기준으로 안내합니다.", 아니면 "출발역이 지정되지 않아 ○○역 기준으로 안내합니다."
- 템플릿 문장은 도구 결과로만 만들므로 후처리 검증을 하지 않는다(`guard_triggered=false`).
- 측정 결과 ([BENCHMARK](./BENCHMARK.md) §4 "응답 속도 옵션과 모델 비교"): `gemini-3.5-flash-lite` + 두 옵션이 운영 타임아웃 3초에서 50/50, P50 1.0초 / P95 1.5초로 유일하게 목표 안이었다. `gpt-5.1` + 두 옵션은 P50 1.6초 / P95 3.0초, 49/50.

### 4.7 HTTP 설정

- CORS: `CORS_ALLOW_ORIGINS`(쉼표 구분, 기본 `*`). 쿠키·인증을 쓰지 않으므로 `*`로 FE 정적 파일(`file://`, 출처 `null`)에서도 호출된다. 배포 때는 FE 주소로 좁힌다.

### 4.8 데이터 소스 (`data/source.py`)

- `DataSource` 인터페이스: 역 목록·역 하나·별칭·미지원 키워드, 역별 엘리베이터·전체 엘리베이터·기준 시각, 계산된 대체역 후보(`alternative_candidates`)·등록된 대체역·인접 역, 계산된 경로(`route_detail`, Mock은 `None`)·Mock 경로(`find_route`), 시간 상수, 출발 시각 역산(`departure_estimate`, Mock은 `None`).
- 구현 선택: 환경 변수 `AI_DATA_SOURCE` = `mock`(기본) / `be`. 테스트는 `use_data_source(가짜)`로 바꿔 끼운다.
- 상태 판정, 안내 문장은 도구에 남아 있다. 출발 시각 역산은 BE면 BE가, Mock이면 도구가 한다 (§2.4). 역산에 넣는 분 값은 어느 쪽이든 도구가 정한다.

**BE 데이터 소스 (`be_source.BeDataSource`, 이슈 #4·#5)** — 설정 `BE_API_BASE_URL`(기본 `http://127.0.0.1:8000`), `BE_API_TIMEOUT_SECONDS`(기본 2.0)

| 기능 | BE API | 변환 |
|---|---|---|
| 역 목록·역 | `GET /api/stations/{id}`를 ID 1부터 차례로 (404가 20번 연속되면 끝) | BE에 목록 API가 없어(검색어 `q` 필수) 처음 필요할 때 읽고 프로세스 동안 캐시. 데모 DB 240역 0.7초. 역 ID는 BE 정수를 문자열로(`"31"`). 역명은 괄호 부제와 끝의 `역`을 뗌(`잠실(송파구청)` → `잠실`, `서울역` → `서울`), 원래 이름은 `display_name`. 역 `limitations`(1호선 안내) 보관 |
| 별칭·미지원 키워드 | — | AI 사전(`mock_data.ALIASES` 등)을 역 이름으로 BE ID에 연결 |
| 역별 엘리베이터 | `GET /api/stations/{id}/elevators` | `가동중`→`AVAILABLE`, `가동 안됨`→`UNAVAILABLE`, `null`→`UNKNOWN`(+`unknown_reason`). `type=None`(BE에 분류 없음), 위치는 `location_description` 원문, 층은 `operation_section`. 합성 설비(`[DEMO/SYNTHETIC]`)는 위치를 "시연용 합성 설비(실제 상태 아님)"로 표시 |
| 전체 엘리베이터 | `GET /api/elevators/status` | 역별로 묶음. 노선 필터는 캐시한 역 목록의 노선으로 (추가 호출 없음) |
| 대체역 | `GET /api/stations/{id}/alternatives` | BE 추천 순 그대로. `distance`→`stops_away`, `same_line`, 후보 역 상태. 도보 거리·시간 없음 |
| 경로·열차 이동 시간 | `GET /api/routes?from=&to=` (이슈 #6, BE #37) | 경로 역·`RIDE`/`TRANSFER` 타임라인·`total_minutes`·`elevator_warning`·`limitations`를 그대로 옮김 (§2.4). 404는 경로 없음(`{"found": False}`), 모양이 다르면 `DataUnavailableError`. Mock 2호선 이름 연결 추정은 없앰 |
| 출발 시각 (역산 모드) | `POST /api/departure-time` (이슈 #6) | 분 값(이동 = BE 경로 `total_minutes`(D17), 여유 = AI 최악 대기 5분, 도보·진입·출구)과 `from_station_id`·`to_station_id`를 보냄. 응답 시각을 KST `HH:MM`으로. `timetable_used`·`selected_train`을 옮기고, `already_past`는 쓰지 않음 (§2.4) |

- 실패 처리: 연결 실패·타임아웃·200/404 외 응답·JSON 해석 실패 → `DataUnavailableError`. 404는 "없음"(`None`). `POST /api/departure-time`은 200 외 응답(404·422 포함)과 시각 필드 누락도 `DataUnavailableError`(422는 AI가 계약과 다른 값을 보낸 것이라 메시지에 밝힘).
- 실행 확인 (2026-11-14): BE `main` 342a9d0 데모 DB 서버에 붙여 8개 질문을 폴백과 `gemini-3.5-flash-lite`(두 옵션 켬)로 확인했다. LLM은 8건 모두 폴백 없이 응답했고, 첫 요청 3.5초(역 목록 읽기 포함), 이후 1.0~1.2초였다. `scripts/run_server.py`는 `AI_DATA_SOURCE=be`면 시작할 때 역 목록을 미리 읽는다.
- 테스트는 데모 서버의 실제 응답을 자른 픽스처(`tests/data/fixtures/be_demo_responses.json`)와 `httpx.MockTransport`로 한다. 출발 시각은 실제 응답 1건(`be_departure_response.json`, 도착 2026-12-01 15:00·이동 20분·나머지 BE 기본값)을 픽스처로 두고, 가짜 BE가 계약 §8.2 공식으로 응답을 만들되 이 녹화 응답과 같은 값을 내는지 먼저 확인한다. 출발 시각 연동은 실제 BE 서버에 붙여 `plan_trip`으로 돌려 보지 않았다 (가짜 BE로만 확인). 경로 API는 녹화한 실제 응답이 없어 계약 §8.4 형식과 BE 문구로 만든 합성 응답(`tests/data/fixtures/be_route_responses.json`, 역 ID·역명·역 상태는 녹화 픽스처에서 가져옴)으로만 확인했다. 녹화한 출발 시각 응답 두 건(2026-11-14)은 `selected_train`이 생기고 `buffer_minutes` 기본값이 5분으로 바뀌기(D15) 전 BE 버전이다.

### 4.9 데이터 미확인·장애 안내 (이슈 #10)

- 도구 결과의 `limitations`는 템플릿·폴백 문장 끝에 "※ …" 줄로 붙이고, LLM 문장에도 없으면 덧붙인다 (§4.1).
- 프롬프트 수칙 11: `UNKNOWN`이거나 `data_fresh=false`면 "정상", "이용 가능"이라고 단정하지 않고, `limitations`는 빼거나 바꾸지 않는다.
- 데이터 소스(BE) 장애: 도구 실행기가 `data_unavailable` 표시를 붙이고, 오케스트레이터는 LLM 문장이 있어도 버리고 "지금은 역·엘리베이터 정보를 확인할 수 없습니다. 이전 정보나 추정값으로 안내하지 않으니 …"로 답한다 (`card_type="data_unavailable"`, `fallback_reason="data_unavailable"`). 역 사전 확인이나 폴백 엔진에서 나도 같다.
- `metadata.data_updated_at`은 고른 결과의 `updated_at`(BE는 설비별 `fetched_at` 중 가장 최근 값, Mock은 Mock 기준 시각)이고, 없으면 `None`이다. 응답 생성 시각으로 채우지 않는다 (§4.2). LLM·폴백 경로 모두 같다.
- 남은 것: "실시간" 표현은 팀 결정 대기.

### 4.10 통합 시나리오 테스트 (이슈 #12)

`tests/llm/test_scenarios_be.py` — 실제 `ChatOrchestrator`를 `BeDataSource`에 붙이고, BE는 `httpx.MockTransport`로 흉내 낸다. BE 응답은 데모 DB 서버 실제 응답 픽스처(`tests/data/fixtures/be_demo_responses.json`)와 출발 시각 API 실제 응답(`tests/llm/fixtures/scenario_be_departure_time.json`), 경로·출발 시각 API 실제 녹화(아래 "2026-11-15" 항목)이다. 가짜 BE는 `POST /api/departure-time`도 BE 계약 공식(§8.2)으로 계산하며, 그 결과가 기록된 실제 응답과 같은지도 검사한다. 역 ID를 받으면 BE 라우터처럼 경로 엘리베이터 경고와 시간표 미사용 이유 문구를 붙인다(시간표 없는 데모 DB 기준). 시나리오는 BE 이슈 #42와 같다.

| 시나리오 | 질문 예 | 폴백 경로 | LLM 경로 (`FakeLLM`, BE 역 ID로 도구 호출) |
|---|---|---|---|
| 1 엘리베이터 상태 | "강남역 엘리베이터 돼?", "사당역 엘리베이터 돼?"(전부 STALE) | ○ | ○ — LLM이 뺀 "※" 한계 안내를 코드가 덧붙임, 고장 역 대체역 보충 |
| 2 대체역 | (강남 다음) "거기 말고 다른 역은?" | ○ — 세션 최근 역, `showAlternative` payload가 BE ID | ○ — 세션 역이 프롬프트에 BE ID로, BE 추천 순(역삼·교대·남부터미널). 지어낸 역(논현)은 검증이 템플릿으로 바꿈 |
| 3 출발 시각 | "신도림에서 잠실까지 3시까지 가려면?" (12:00) | ○ — 14:06 출발, 14:21 탑승 (BE 경로 36분) | ○ — LLM 문장, 역 사전 확인 + 템플릿, 지어낸 시각 교체 |
| 4 고장·점검 목록 | "지금 고장난 역 알려줘", "2호선에서 엘리베이터 고장 난 역 목록 보여줘" | ○ — 강남·선릉, 미확인 설비 안내 | ○ — 한계 안내 덧붙임 |
| 5 경로 조회 | "역삼에서 고속터미널까지 3시까지 가려면?" (2호선 → 교대 환승 → 3호선, 11분) | ○ — 14:31 출발, 출발 시각 API에 이동 11분·역 ID 30/77, 경유 강남 고장 경고, BE 경로·출발 시각 문구 그대로 | ○ — LLM 문장에 경고·한계 안내 덧붙임, 도구 결과의 `route_source`·환승역·`elevator_warning`. 경로 없음(404) → `trip_unsupported` |
| BE 장애 | 연결 실패, 엘리베이터 조회만 5xx | ○ | ○ (역 사전 확인 포함) — `data_unavailable`, `fallback_reason="data_unavailable"` |
| 지원 범위 밖 | "판교역 엘리베이터 돼?" | ○ | ○ — `out_of_coverage` |

- 출발 시각은 계산 방식이 BE API로 바뀌어도 통과하도록 시각·카드·액션과 한계 안내가 있는지만 확인하고 문구는 고정하지 않는다.

---

## 5. 명세와 현재 구현의 차이

| 항목 | 명세 / 설계 검토 | 현재 구현 |
|---|---|---|
| LLM 타임아웃 | 2.5초 (명세 §1, §6.2) | `config.py` 기본값 3.0초, 호출 1회 기준 — **팀 확정 필요** |
| 오케스트레이터 비동기 처리 | `async/await` (명세 §9 Phase 2) | 동기 함수 + FastAPI 스레드 풀 |
| 후처리 검증 대상 | 역명·시각 정규식 (명세 §6.1) | 같음. 추가로 `N시 반`, `N시` 형식과 오전/오후 12시간 차이 허용 (§4.5) |
| `ChatResponseMetadata` | `engine`, `latency_ms`, `fallback_used`, `data_updated_at` | `fallback_reason`, `guard_triggered` 추가, `data_updated_at`은 모르면 `null` (명세 §4.2 반영) |
| 커버리지 판정 | 역 단위 목록 (설계 검토 §2.2) | `STATIONS` 등록 여부 + 키워드 블랙리스트 |
| 엘리베이터 이용 가능 판정 | 이동 방향에 필요한 구간 기준 (설계 검토 §2.3) | 확인된 설비 중 불가 개수 기준 (BE 계약 §6.2와 같음). BE에 설비 위치 분류·방향이 없어 구간 판정 불가 |
| 고장 우회 | 출발·환승·도착, 그래프에서 고장 노드 제외 | 대체역은 출발·도착만. BE 연동 시 BE 대체역 API(인접 간선 BFS), Mock은 등록된 안내 또는 인접 역. 경유·환승역은 BE 경로 경고로 알리기만 하고 경로를 바꾸지 않음 (BE 경로도 엘리베이터 상태를 경로 선택에 쓰지 않음) |
| 경로 탐색 | 다익스트라, 환승 포함 | BE 연동 시 BE `GET /api/routes`(역-노선 그래프 최단 경로, 환승 포함, 역 간 2분·환승 5분 가정). Mock은 2호선 순환선 방향 비교만 |
| 오전/오후 모호성 | 현재 시각 이후 가장 가까운 시각 (설계 검토 §2.1) | 1~6시는 오후 고정, 지난 시각은 다음 날 |
| 첫차/막차 | 범위 밖이면 예외 응답 (설계 검토 §2.4) | 미반영. BE 응답에 운행 시간 밖을 알리는 값이 없어 판정하지 않음 (§2.4, §6) |
| 데이터 신선도 | 실제 갱신 시각 | BE 연동 시 설비 `fetched_at`과 BE의 `data_fresh` 판정(TTL 120분, 임시). Mock은 현재 − 5분 고정값 |
| 출발 시각 대기 시간 | 배차 간격 + 안전 여유 | **5분으로 통일**(팀 결정 2026-11-14, 2호선 배차 3 + 여유 2). BE 기본값도 5분으로 바뀌었다(BE D15, 2026-11-15 BE 계약 §8.2와 `app/departure_defaults.py`에서 확인). 도보 7·진입 3·출구 3도 같다. AI는 값을 계속 명시해 보낸다 (§2.4) |

---

## 6. 남은 제약 (Mock 단계라 의도적으로 둔 것)

- Mock 데이터 소스의 경로는 2호선 순환선만. 순환선 밖 역은 `is_possible=False`로 정직하게 거절한다. BE 데이터 소스는 BE 경로 API를 쓴다.
- **운행 시간 밖(첫차 전·막차 후)을 판정하지 못한다.** BE 출발 시각·경로 응답에 운행 시간 밖을 알리는 값이 없다. 환승 경로는 BE가 시간표를 쓰지 않아 새벽 시각에도 추정 출발 시각이 나온다. 한 노선 경로의 "시간표에서 … 맞는 열차를 찾지 못해" 문구는 시간표 없음·방향 불명과 구분되지 않아 판정 근거로 쓰지 않고 그대로 전한다. BE에 기계가 읽을 수 있는 사유(예: 열차 선택 결과 코드)나 운행 시간 정보를 요청해야 한다.
- 대체역 거리 정보가 없는 곳(방배, 신대방, 인접 역 자동 선택, BE 대체역 전부)은 거리·도보 시간을 안내하지 않는다.
- BE 데이터 소스의 역 목록은 프로세스 동안 캐시한다. BE 역 시드가 바뀌면 AI 서버를 다시 시작해야 한다.
- BE 경로 API(#37) 연동으로 `BE_ROUTE_ESTIMATE` 설정(경로 API 전에 경로 질문을 막거나 Mock 2호선으로 추정)은 없앴다 (2026-11-15, 이슈 #6). `AI_DATA_SOURCE` 기본값은 그대로 `mock`이다.
- BE 데모 DB는 엘리베이터 상태가 대부분 `STALE`이라, 시연 답변의 대부분은 "확인할 수 없음"이다 (BE `docs/demo-handoff.md`).
- 폴백의 역 추출은 띄어쓰기가 없는 문장("신도림에서강남역")에서 앞 역을 놓칠 수 있다. LLM 경로는 `resolve_station`이 역 이름만 받으므로 영향 없음.
- 세션은 서버 프로세스 메모리에만 있다. 서버를 여러 대 띄우거나 재시작하면 이어지지 않는다.
- 예전 기본 설정(`gpt-5.1`, 옵션 끔)은 한 질문에 P50 약 5.5초가 걸려, 호출당 3초 타임아웃에서 32~44%가 폴백으로 넘어갔다 ([BENCHMARK](./BENCHMARK.md) §4). 현재 기본값은 목표 안에 든 조합이다. 타임아웃 값(명세 2.5초 vs 3.0초)은 아직 결정 전이다.

---

## 7. 변경 이력

### 2026-11-15 — `plan_trip`을 BE 경로·출발 시각 API로 교체 (이슈 #6)

| 추가·변경 | 내용 |
|---|---|
| `src/data/be_source.py`, `source.py`, `mock_source.py` | `route_detail()`: BE `GET /api/routes`를 AI 형식으로 (404 → 경로 없음, 모양이 다르면 `DataUnavailableError`). Mock은 `None`. `departure_estimate()`에 `from_station_id`·`to_station_id` 추가, `selected_train`·`elevator_warning` 형식 확인. Mock 2호선 이름 연결 경로(`_mock_id`)와 `route_estimate` 인자 삭제 |
| `src/tools/trip.py` | BE 경로면 경로·환승·소요 시간을 BE 값으로, 출발 시각 API에 경로 `total_minutes`와 역 ID를 보냄(D17). 경유·환승역 엘리베이터 경고를 `warnings`/`limitations`로. BE `limitations`는 서버 시계 기준 "이미 지났음"과 역 ID 없을 때의 미확인 문구만 빼고 그대로. 경로 없음 → `is_possible=False` (§2.4) |
| `src/models.py` | `RouteInfo.transfer_station_ids`, `PlanTripOutput.route_source`·`elevator_warning`(`RouteElevatorWarning`)·`timetable_used`·`selected_train`(`SelectedTrain`) |
| `src/llm/response_builder.py`, `orchestrator.py`, `src/fallback/engine.py` | 경로 요약에 환승 횟수. LLM 문장에 빠진 `plan_trip` 경고를 "⚠️ …"로 덧붙임 |
| `src/config.py`, `.env.example` | `BE_ROUTE_ESTIMATE` 삭제 (`AI_DATA_SOURCE` 기본값 `mock`은 그대로) |
| 테스트 | BE 경로·환승·경고·404·형식 오류·시간표 열차·기본값 일치 테스트, 통합 시나리오 5 구현(skip 해제). 처음에는 계약 형식 합성 픽스처로 만들었고, 이슈 #12에서 실제 녹화로 교체했다(아래 이슈 #12 항목) |

### 2026-11-15 — 실제 BE 녹화 픽스처로 교체 (이슈 #12)

| 추가·변경 | 내용 |
|---|---|
| 녹화 | dev.db 복사본으로 띄운 BE(main 5ff20c1)의 실제 응답을 2026-11-15에 녹화. 요청 본문은 `plan_trip`이 실제로 보내는 값(대기 5분, 역 ID 포함). 서버 시계 의존("이미 지났음")을 피하려고 도착 시각은 모두 미래(2026-12-01~06)로 잡았고 각 `_source`에 적었다 |
| 픽스처 | `tests/data/fixtures/be_route_responses.json`(경로 실제 녹화로 교체), `be_departure_response.json`, `tests/llm/fixtures/scenario_be_departure_time.json`(새로 녹화), `be_departure_plan_responses.json`(신규) |
| `src/tools/trip.py` | 경로 `total_minutes`를 `travel_minutes`로 보낸 경우 BE limitation "예상 이동 시간은 외부에서 입력한 값이며 경로 탐색으로 검증되지 않음"은 전하지 않는다(값의 출처가 BE 경로 탐색). 역 ID 없이 보낸 요청에서는 그대로 전한다 |
| 확인 | 단일 노선 `timetable_used=true`+`selected_train`, 환승 `timetable_used=false`, 일요일 04:00 시간표 없음 추정 폴백. 03:00 도착은 BE가 "맞는 열차 없음"이 아니라 그날 막차(00:46 탑승)를 고르고 AI는 그대로 전한다 |
| 확인하지 않은 것 | "경로 없음" 404 녹화(이 DB는 모든 역이 연결됨, 합성 응답 사용), 엘리베이터 고장이 섞인 경로 경고 녹화(복사본에 상태 데이터 없음, 변형 응답으로 테스트), 실시간·공휴일, 03:00 막차 선택이 BE 의도인지. `be_demo_responses.json`(역·엘리베이터)은 옛 BE(342a9d0) 기준이라 강남 `UNAVAILABLE`이고 새 경로 녹화는 `UNCONFIRMED`로 서로 다르다 |
| 테스트 | 총 324건 통과 |

### 2026-11-14 — 팀 결정 반영 (모델·옵션 기본값, 경로 질문 막기, 대기 시간 5분)

| 추가·변경 | 내용 |
|---|---|
| `src/config.py`, `.env.example` | 기본값 변경: `FACTCHAT_MODEL=gemini-3.5-flash-lite`, `LLM_PRERESOLVE=true`, `LLM_ANSWER_MODE=template` (§4.6). 새 설정 `BE_ROUTE_ESTIMATE`(기본 false) |
| `src/data/be_source.py`, `source.py`, `src/tools/trip.py` | `BE_ROUTE_ESTIMATE=false`면 BE 경로 API(#37) 전에는 경로·출발 시각 질문에 "안내하지 않습니다"로 답함. 역·엘리베이터·대체역은 그대로 |
| `conftest.py` | 테스트가 운영 기본값에 의존하지 않게 LLM 옵션을 고정 (autouse) |
| 테스트 | 기본 설정 3건(`tests/api/test_config_defaults.py`), 경로 막기 1건 추가 (총 296건, 1건 skip) |
| 문서 | 대기 시간 5분 통일 결정 기록 (BE #14에 기본값 변경 요청), 역 목록 방식 유지 결정 |

### 2026-11-14 — BE 출발 시각 연동 (이슈 #6 일부), 통합 시나리오 (이슈 #12), 데이터 기준 시각 (이슈 #10)

세 작업을 병렬로 만들어(브랜치 `feat/departure-api`, `test/integration-scenarios`, `fix/data-updated-at`) 한 브랜치로 합쳤다.

| 추가·변경 | 내용 |
|---|---|
| `src/data/source.py`, `mock_source.py`, `be_source.py` | `departure_estimate()`: Mock은 None(직접 계산), BE는 `POST /api/departure-time`. 분 값은 모두 AI 값을 명시해서 보냄(대기 5분 등) |
| `src/tools/trip.py`, `src/models.py` (`PlanTripOutput.time_source`) | 역산 모드·경로 있음·출발≠도착이면 BE 결과 시각 사용 (`time_source="BE"`). 지각 판정은 AI 기준 시각. BE limitations 중 엘리베이터 미확인·이미 지났음 문구는 전하지 않음 (§2.4) |
| `src/models.py` (`ChatResponseMetadata.data_updated_at`), `src/llm/orchestrator.py`, `response_builder.py`, `src/fallback/engine.py` | `data_updated_at`은 데이터 수집 시각, 모르면 `null` (예전에는 응답 생성 시각). 폴백 경로도 도구의 `updated_at`을 씀 (§4.2) |
| `src/fallback/engine.py` | 대체역 후보가 여럿이면(BE) "다른 후보"도 안내 — LLM 경로 템플릿과 맞춤 (통합 시나리오에서 발견) |
| `tests/llm/test_scenarios_be.py` | 실제 BE 응답 픽스처 기반 `/api/chat` 시나리오 4종 × 폴백·LLM, BE 장애·범위 밖. 경로 조회는 skip (§4.10) |
| 실행 확인 | BE 데모 서버로 8개 질문 재확인: 출발 시각이 BE로 계산되고(14:06, 이전과 같음) `data_updated_at`에 BE `fetched_at`이 들어감. LLM 8건 폴백 없음 |
| 테스트 | 출발 시각 22건, 기준 시각 27건, 시나리오 25건(+skip 1), 병합 확인 1건 추가 (총 292건 통과, 1건 skip) |

### 2026-11-14 — BE 계약 리뷰 (이슈 #1), 상태 규칙 (이슈 #2), BE API 연동 (이슈 #4, #5), 미확인 안내 (이슈 #10 일부)

| 추가·변경 | 내용 |
|---|---|
| BE #14 코멘트 | R9·R10 답변, `buffer_minutes` 0분 의견, 역명·검색·위치 분류 차이, 노선 필터 요청, Windows 실행 문제(tzdata, alembic.ini 인코딩) |
| `docs/DATA_SPEC.md` §8·§9 | BE API 호출·계산은 BE로 확정, BE 응답과 AI 도구의 대응 |
| `src/models.py`, 명세 §3 | 설비 `MAINTENANCE` 삭제·`UNKNOWN` 추가(`unknown_reason`, `fetched_at`), `type` 선택. 역 상태에 `data_fresh`, `total_count`, `unknown_count`, `limitations`. 고장 목록 `limitations`·`unknown_count`. 대체역 거리 선택·`stops_away`·`same_line`·`alternative_status`. `plan_trip`에 `estimated`, `limitations` |
| `src/tools/elevator.py` | BE 규칙 판정(`judge`), 공용 안내 문장(`describe_status`), 표준 limitations 문구 |
| `src/tools/trip.py` | `find_alternatives`(데이터 소스 계산 결과 우선, 기본 거리값 제거, 미확인 대체역 안내), 미확인 역에는 대체역 미부착, 추정 계산 안내 |
| `src/data/be_source.py`, `config.py`, `.env.example` | BE API 데이터 소스 (`AI_DATA_SOURCE=be`, `BE_API_BASE_URL`, `BE_API_TIMEOUT_SECONDS`) |
| `src/data/source.py`, `tools/registry.py`, `llm/orchestrator.py`, `llm/response_builder.py` | `DataUnavailableError` → `data_unavailable` 응답. LLM 문장에 limitations 강제 |
| `src/llm/prompts.py`, 명세 §5.1 | 수칙 11(미확인 상태 단정 금지, limitations 전달), 기본 출발역을 세션 컨텍스트로 (Mock ID `sindorim` 고정 제거) |
| `src/fallback/engine.py` | 공용 안내 문장 사용, "실시간"·"모든 역 정상" 표현 제거, 기본 출발역을 이름으로 |
| `scripts/run_server.py` | BE 데이터 소스면 시작할 때 역 목록을 미리 읽음 |
| 벤치마크 (Mock) | 폴백 50/50. `gemini-3.5-flash-lite` 3초: 사전 확인+템플릿 50/50 P95 1.83초, 사전 확인+LLM 답변 49/50 |
| 테스트 | 상태 규칙 15건, BE 데이터 소스 15건, 장애·limitations 5건 추가, 기존 2건 수정 (총 217건) |

### 2026-11-13 — 폴백 오류 수정 (이슈 #18), 응답 속도 옵션 (이슈 #17), CORS (이슈 #14 일부)

| 추가·변경 | 내용 |
|---|---|
| `src/fallback/engine.py` | "○○에서 그 역까지"면 말한 역이 출발·최근 역이 도착 (반대 방향 안내 수정, 벤치마크 CX09). 고장 목록에서 `N호선` 필터 반영 (EL06). 폴백 기준선 48/50 → 50/50 |
| `src/llm/orchestrator.py`, `prompts.py`, `config.py` | `LLM_PRERESOLVE`, `LLM_ANSWER_MODE` 옵션 (§4.6). 기본값은 기존 동작 |
| `src/api/app.py`, `config.py` | CORS 허용 (`CORS_ALLOW_ORIGINS`, 기본 `*`) |
| `scripts/run_benchmark.py` | `--preresolve`, `--answer-mode` |
| `docs/BENCHMARK.md` | 옵션·모델 조합 7개 측정. `gemini-3.5-flash-lite` + 두 옵션: 3초 타임아웃에서 50/50, P95 1.5초 |
| `src/tools/elevator.py` | `get_elevator_status`가 지원 역 목록에 없는 ID에 오류를 돌려줌 (역 사전 확인 측정에서 LLM이 "판교"로 상태를 조회해 지원 역처럼 안내한 문제) |
| `src/llm/client.py` | 게이트웨이가 준 assistant 메시지 원본을 다음 호출에 그대로 돌려줌. Gemini는 `tool_calls[].extra_content.google.thought_signature`가 빠지면 HTTP 400을 내서, 도구 결과를 붙인 두 번째 호출이 모두 실패했음 |
| 테스트 | 폴백 3건, 오케스트레이터 7건, API 2건, 도구 1건, 클라이언트 2건 추가 (총 182건) |

### 2026-11-13 — 50문항 벤치마크와 프롬프트 튜닝 (이슈 #13), BE OA-15994 확인 결과 반영 (이슈 #1)

| 추가·변경 | 내용 |
|---|---|
| `src/eval/cases.json`, `benchmark.py`, `scripts/run_benchmark.py` | 명세 §8.1 구성의 50문항 평가셋과 채점(도구·슬롯 F1·카드·문장·응답 시간) |
| `docs/BENCHMARK.md` | 실행 방법, 채점 기준, 결과. 폴백 기준선 48/50 (실패: 노선 필터 무시 EL06, 지칭어와 새 역이 함께 나올 때 출발·도착 뒤바뀜 CX09). `gpt-5.1` 최종 50/50(타임아웃 30초) · 47/50(운영 3초), P50 약 5.5초 |
| `src/llm/prompts.py`, 명세 §5.1 | 시스템 프롬프트 수칙 7개 → 10개: 도구 없이 답하지 않기, 역 ID 추측 금지, 지칭어 = 최근 역, 대체역은 detours만, 시각 반올림 금지, 필드 이름 노출 금지 |
| `src/llm/orchestrator.py` | 가드에 걸렸는데 템플릿이 도움말뿐이면 폴백 엔진으로 다시 답함 (`fallback_reason="guard_rejected"`) |
| `src/llm/guard.py` | 일반 표현 추가 (이용·정차·열차·철도·기차·출발지·도착지·권장·우회·추천 + 역) |
| `scripts/run_benchmark.py` | `--timeout`, `--model` (측정용 덮어쓰기), 문항별 진행 표시, 가드 발동 표현 기록 |
| `docs/DATA_SPEC.md` §4.2·§4.6·§8 | BE가 확인한 OA-15994 API 응답(갱신 시각·고유 ID 없음, 하루 사이 상태 100건 변경)과 계약 결정 항목 |
| `src/llm/orchestrator.py` (CX08) | 고장 역의 대체역 조회가 빠지면 코드가 보충 조회하고, 문장에 대체역이 없으면 한 줄 덧붙임 |
| 테스트 | `tests/eval/test_benchmark.py` 56건, `test_orchestrator.py` 6건 추가 (총 167건) |

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
