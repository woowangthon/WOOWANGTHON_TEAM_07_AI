# [CARD-003] 프로토타입 모듈 인터페이스 계약 (병렬 구현용)

모든 파일은 이 계약만 보고 독립적으로 작성된다. **계약에 없는 전역 이름/DOM ID에 의존하지 말 것.**

- 실행: `Prototype/index.html`을 브라우저에서 직접 연다 (file://). ES module 금지, 빌드 금지, fetch 금지.
- 각 JS 파일은 IIFE로 감싸고 `window.X = ...` 로 하나의 전역만 노출한다.
- 스크립트 로드 순서 (index.html 하단, defer 없음):
  `mock-data.js → elevator-service.js → time-calculator.js → metro-map.js → ai-agent.js → app.js`
- 초기화는 `app.js`가 `DOMContentLoaded`에서 수행한다. 다른 모듈은 로드 시 DOM을 건드리지 않는다.
- 언어: UI 문구는 한국어.

---

## 1. `window.MOCK` (js/mock-data.js)

```js
window.MOCK = {
  viewBox: { width: 1600, height: 1400 },     // SVG 좌표계
  lines: {
    "2": {
      id: "2", name: "2호선", short: "2", color: "#00A84D",
      loop: true,                               // true면 segments[0]의 마지막 역 → 첫 역 연결
      segments: [ ["cityhall","euljiro1ga", ...], ["seongsu","yongdap", ...] ] // 지선은 별도 segment
    },
    ...
  },
  stations: {
    "gangnam": { id: "gangnam", name: "강남", x: 1010, y: 980, lines: ["2","SB"] },
    ...
  },
  elevators: {        // 2호선 전 역 + 주요 환승역. 키가 없는 역 = 데이터 없음(UNKNOWN)
    "gangnam": [
      { id: "gangnam-e1", location: "1번 출구", type: "exit", floors: "지상 ↔ B1 대합실",
        status: "AVAILABLE", reason: "" },
      { id: "gangnam-e3", location: "2호선 승강장 (내선)", type: "platform", floors: "B1 ↔ B2",
        status: "AVAILABLE", reason: "" },
      { id: "gangnam-e5", location: "신분당선 환승통로", type: "transfer", floors: "B2 ↔ B4",
        status: "MAINTENANCE", reason: "정기 점검 (14:00~16:00)" }
    ]
  },
  // status 값: "AVAILABLE" | "UNAVAILABLE"(고장) | "MAINTENANCE"(점검)  — 후자 둘 다 '이용 불가'
  headways: { "1": 5, "2": 3, ... },           // 분 단위 배차 간격 (모든 line id 포함)
  firstTrain: "05:30",
  lastTrain: "24:00",
  timing: {
    minutesPerStop: 2,       // 역간 열차 이동
    transferMinutes: 5,      // 환승 1회
    walkToStation: 7,        // 집/현재위치 → 역 도보
    stationEntry: 3,         // 역사 진입 + 엘리베이터
    exitWalk: 3,             // 도착역 → 출구
    walkPerStopMinutes: 12   // 인접 역 간 지상 도보 추정
  },
  alternativeTips: {         // "출발id>대체id": 우회 안내 문구 (핵심 쌍만 하드코딩)
    "gangnam>yeoksam": "역삼역 3번 출구로 나와 테헤란로를 따라 강남역 방향으로 도보 약 10분 (800m). 146·341번 버스로 1정거장.",
    ...
  },
  demoScenarios: [           // 시연 제어판 버튼
    { id: "gangnam", label: "강남역 엘리베이터 전체 고장", stationId: "gangnam" },
    { id: "cityhall", label: "시청역 엘리베이터 전체 고장", stationId: "cityhall" },
    ...
  ]
};
```

### 노선 ID (고정)
| id | name | short | color |
|---|---|---|---|
| 1 | 1호선 | 1 | #0052A4 |
| 2 | 2호선 | 2 | #00A84D |
| 3 | 3호선 | 3 | #EF7C1C |
| 4 | 4호선 | 4 | #00A5DE |
| 5 | 5호선 | 5 | #996CAC |
| 6 | 6호선 | 6 | #CD7C2F |
| 7 | 7호선 | 7 | #747F00 |
| 8 | 8호선 | 8 | #E6186C |
| 9 | 9호선 | 9 | #BDB092 |
| SB | 신분당선 | 신분당 | #D4003B |
| SU | 수인분당선 | 수인분당 | #FABE00 |
| AREX | 공항철도 | 공항 | #0090D2 |
| GJ | 경의중앙선 | 경의중앙 | #77C4A3 |

### 역 ID 규칙
snake_case 로마자. 환승역은 **모든 노선에서 같은 id**를 쓴다. 다음 id는 반드시 존재해야 한다 (다른 모듈/시연이 참조):
`cityhall, euljiro1ga, euljiro3ga, euljiro4ga, ddp(동대문역사문화공원), sindang, sangwangsimni, wangsimni, hanyang_univ, ttukseom, seongsu, konkuk(건대입구), guui, gangbyeon, jamsilnaru, jamsil, jamsilsaenae, sports_complex(종합운동장), samseong, seolleung, yeoksam, gangnam, gyodae, seocho, bangbae, sadang, nakseongdae, seoul_univ(서울대입구), sillim, guro_digital(구로디지털단지), daerim, sindorim, mullae, yeongdeungpo_gu(영등포구청), dangsan, hapjeong, hongik(홍대입구), sinchon, ewha(이대), ahyeon, chungjeongno, seoul(서울역), jongno3ga, express_bus(고속터미널), yeouido, gimpo_airport, gongdeok, dongdaemun, isu(총신대입구/이수), suseo, yangjae, pangyo`

---

## 2. `window.ElevatorService` (js/elevator-service.js)

```js
ElevatorService.getStation(id)                 // → station 객체 | null
ElevatorService.getAllStations()               // → station[]
ElevatorService.findStationByName(text)        // 부분/퍼지 매칭, "역" 접미사·공백 무시, 별칭(홍대→hongik, 건대→konkuk, 서울대→seoul_univ, 동대문운동장→ddp 등) → station | null
ElevatorService.findStationsInText(text)       // 텍스트에 등장하는 역들을 등장 순서대로 → station[]
ElevatorService.getLine(lineId)                // → line 객체
ElevatorService.getElevators(stationId)        // → elevator[] (없으면 [])
ElevatorService.hasElevatorData(stationId)     // → boolean
ElevatorService.getStationStatus(stationId)    // → "AVAILABLE" | "PARTIAL" | "UNAVAILABLE" | "UNKNOWN"
                                               //   전부 정상=AVAILABLE, 일부 불가=PARTIAL, 전부 불가=UNAVAILABLE, 데이터 없음=UNKNOWN
ElevatorService.getStatusLabel(status)         // → "정상 운행" | "일부 이용 불가" | "이용 불가" | "정보 없음"
ElevatorService.toggleElevator(stationId, elevatorId)   // AVAILABLE ↔ UNAVAILABLE (reason "시연용 고장 전환")
ElevatorService.setStationBroken(stationId, broken)     // true: 전부 UNAVAILABLE / false: 원래 MOCK 초기값으로 복원
ElevatorService.reset()                        // 전체 초기값 복원
ElevatorService.getNeighbors(stationId)        // → [{ id, lineId }] 인접 역 (loop 반영)
ElevatorService.findAlternativeStation(stationId)
   // origin 제외, BFS로 가장 가까운 AVAILABLE 역 (없으면 PARTIAL). UNKNOWN은 후보 제외.
   // → { station, stops, path: [ids...], lineId, walkMinutes, tip } | null
   //   tip: MOCK.alternativeTips["origin>alt"] 있으면 사용, 없으면 자동 문구 생성
ElevatorService.getOutages()                   // → [{ station, status, broken: elevator[] }] PARTIAL/UNAVAILABLE 역 목록
ElevatorService.subscribe(fn)                  // 상태 변경 시 fn(stationId|null) 호출. 반환값: unsubscribe 함수
```
상태는 MOCK.elevators를 deep copy한 내부 상태로 관리한다 (reset 가능하도록).

## 3. `window.TimeCalculator` (js/time-calculator.js)

```js
TimeCalculator.findRoute(originId, destId)
  // 가중치 다익스트라 (역간 minutesPerStop, 노선 변경 시 transferMinutes)
  // → { path: [ids], legs: [{ lineId, from, to, stops }], stops, transfers, rideMinutes } | null
TimeCalculator.calculateDeparture({ originId, destId, arriveBy /* "HH:MM" | null */, now /* Date, 기본 new Date() */ })
  // arriveBy 있으면 역산, 없으면 지금 가장 빨리 출발하는 경우
  // 열차는 firstTrain 기준 headway 격자 + (원 노선상 역 index × minutesPerStop) 오프셋으로 출발한다고 가정
  // → {
  //   leaveAt: "HH:MM",        // 집/현재 위치에서 출발해야 하는 시각
  //   boardAt: "HH:MM",        // 열차 탑승 시각
  //   arriveAt: "HH:MM",       // 도착역 출구 도착 시각
  //   minutesFromNow: number,  // leaveAt까지 남은 분 (음수면 이미 늦음)
  //   late: boolean,           // arriveBy를 지킬 수 없으면 true
  //   route,                   // findRoute 결과
  //   breakdown: { walk, entry, wait, ride, transfer, exitWalk },   // 분
  //   timeline: [{ time: "HH:MM", label: "집에서 출발", icon: "🚶" }, ...],
  //   elevatorWarnings: [{ stationId, status, alternative }]        // 출발/도착역이 PARTIAL/UNAVAILABLE이면
  // } | null
TimeCalculator.parseKoreanTime(text, now)  // "3시", "오후 3시 30분", "15:00", "3시 반" → "HH:MM" | null
                                           // 오전/오후 없고 1~6시면 오후로 간주
TimeCalculator.formatTime(date)            // → "HH:MM"
TimeCalculator.addMinutes("HH:MM", n)      // → "HH:MM"
```

## 4. `window.MetroMap` (js/metro-map.js + css/map.css)

```js
MetroMap.init(containerEl, { onStationClick(stationId) })  // containerEl = #mapContainer, 내부에 <svg class="metro-svg"> 생성
MetroMap.refreshStatuses()          // 엘리베이터 상태 뱃지 갱신 (init 시 ElevatorService.subscribe로 자동 호출)
MetroMap.focusStation(id, { zoom }) // 해당 역 중심으로 부드럽게 이동 + 선택 표시
MetroMap.selectStation(id | null)
MetroMap.highlightAlternative(fromId, toId, path)  // 점선 애니메이션 우회 경로 + 대체역 펄스, 두 역이 보이게 뷰 조정
MetroMap.clearHighlights()
MetroMap.setLineFilter(lineId | null)  // 선택 노선 외 dimmed
MetroMap.zoomIn(); MetroMap.zoomOut(); MetroMap.resetView();
```
- 줌/팬: 포인터 드래그, 휠 줌, 두 손가락 핀치(Pointer Events). 드래그와 탭 구분 (이동 < 6px이면 탭).
- 역 표시: 일반역 작은 흰 원+노선색 테두리, 환승역 더 큰 흰 원+검정 테두리. 라벨은 줌 수준에 따라 표시(환승역은 항상).
- 엘리베이터 데이터가 있는 역은 상태 점(초록/주황/빨강) 표시. UNAVAILABLE은 펄스.
- SVG 내부 스타일은 **css/map.css** 에만 작성 (style.css 건드리지 않음).

## 5. `window.AIAgent` (js/ai-agent.js)

```js
AIAgent.processMessage(text)  // → Promise<Response>, 400~900ms 지연으로 "생각 중" 연출
// Response = {
//   text: string,                       // 말풍선 본문 (평문, 줄바꿈 \n 허용)
//   html: string | "",                  // 리치 카드 HTML (아래 공용 클래스 사용)
//   toolCalls: [{ name, args }],        // 표시용. name ∈ check_elevator | find_alternative | calculate_departure | list_outages | find_route
//   suggestions: string[],              // 후속 질문 칩
//   actions: [{ label, type, payload }] // type ∈ "openStation"(payload: stationId) | "showAlternative"(payload:{from,to,path}) | "openTime"(payload:{originId,destId,arriveBy})
// }
AIAgent.getWelcome()       // → Response (첫 인사 + 예시 칩)
```
- 인텐트: 엘리베이터 상태 / 고장·대체역 / 출발 시각 / 경로 / 고장 현황 목록 / 인사·도움말 / 이해 못함.
- 출발 시각 질의에서 출발역이 없으면 기본 출발역 `App.getDefaultOrigin()` 대신 **"sindorim"** 을 기본값으로 쓰고 문구로 밝힌다 (App 의존 금지).
- 대화 문맥: 직전 언급 역을 기억해 "거기 엘리베이터는?" 처리.

## 6. `window.App` (js/app.js)
UI 총괄. 탭 전환, 바텀 시트, 검색, 엘리베이터 목록, 출발 계산 폼, 챗 렌더링, 시연 제어판, 토스트, 상태바 시계.
```js
App.openStation(stationId)          // 바텀시트 열기 + 지도 포커스
App.switchTab(name)                 // "map" | "elevator" | "time" | "chat"
App.toast(msg)
```
- 챗: AIAgent Response를 렌더링 (봇 말풍선 + `toolCalls`를 `.tool-chip`으로 표시 + `html` 카드 + `actions` 버튼 + `suggestions` 칩). 타이핑 인디케이터 표시.
- 바텀시트 역 상세: 역명, 노선 뱃지, 상태 pill, 엘리베이터 목록(각 항목 토글 버튼), "역 전체 고장 전환 (시연)" 버튼, 이용 불가 시 대체역 박스 + "지도에서 우회 경로 보기", "이 역까지 출발 시각 계산", "AI에게 물어보기".

---

## 7. index.html DOM ID (고정)

| ID / 선택자 | 역할 |
|---|---|
| `#phone` | 폰 프레임 (데스크톱) / 전체화면 (모바일). `body.fullscreen` 이면 프레임 제거 |
| `#statusTime` | 상태바 시계 |
| `#searchInput`, `#searchResults` | 상단 역 검색 + 드롭다운 |
| `#demoBtn` | 헤더의 시연 제어판 열기 버튼 (모바일용) |
| `.view[data-view="map"]` 등 `#view-map`, `#view-elevator`, `#view-time`, `#view-chat` | 탭 화면. 활성 화면에 `.active` |
| `#mapContainer` | 노선도 SVG 컨테이너 |
| `#zoomIn`, `#zoomOut`, `#zoomReset` | 지도 컨트롤 |
| `#lineFilter` | 노선 필터 칩 가로 스크롤 영역 (app.js가 채움) |
| `#elevatorSummary`, `#elevatorFilter`, `#elevatorList` | 엘리베이터 탭 |
| `#timeForm`, `#originSelect`, `#destSelect`, `#swapBtn`, `#arriveBy`, `#modeNow`(checkbox: 지금 바로 출발), `#timeResult` | 출발 계산 탭 |
| `#chatMessages`, `#chatSuggestions`, `#chatForm`, `#chatInput` | 챗봇 탭 |
| `#sheetBackdrop`, `#bottomSheet`, `#sheetContent` | 바텀 시트. 열림 시 `#bottomSheet.open`, `#sheetBackdrop.show` |
| `.tab-bar .tab[data-tab]` | 하단 탭. 활성에 `.active` |
| `#toast` | 토스트. 표시 시 `.show` |
| `#demoPanel`, `#demoScenarioList`, `#demoReset`, `#fullscreenToggle` | 데스크톱 좌측 시연 제어판 (app.js가 시나리오 버튼 채움) |

## 8. 공용 CSS 클래스 (style.css가 스타일링, app.js/ai-agent.js가 사용)

디자인 토큰 (`:root`): `--bg #F3F5F9`, `--surface #FFFFFF`, `--text #111827`, `--muted #6B7280`, `--border #E5E7EB`, `--primary #2563EB`, `--ok #16A34A`, `--warn #F59E0B`, `--bad #DC2626`, `--radius 16px`. 폰트: Pretendard → system-ui.

| 클래스 | 용도 |
|---|---|
| `.card`, `.card-title`, `.card-sub`, `.card-row` | 일반 카드 |
| `.line-badge` (style=`--line-color:#hex`) | 노선 원형/알약 뱃지, 텍스트는 line.short |
| `.status-pill.ok / .warn / .bad / .unknown` | 상태 알약 (AVAILABLE/PARTIAL/UNAVAILABLE/UNKNOWN) |
| `.elev-list`, `.elev-item`, `.elev-item.down`, `.elev-icon`, `.elev-loc`, `.elev-meta`, `.elev-reason` | 엘리베이터 목록 |
| `.alt-box`, `.alt-title`, `.alt-tip` | 대체역 안내 박스 (경고 톤) |
| `.timeline`, `.timeline-item`, `.timeline-time`, `.timeline-label`, `.timeline-icon` | 출발 타임라인 |
| `.big-time`, `.big-time-sub` | 큰 시각 표시 |
| `.route-legs`, `.route-leg` | 경로 구간 (노선뱃지 + "A → B · n개역") |
| `.btn`, `.btn-primary`, `.btn-danger`, `.btn-ghost`, `.btn-sm`, `.btn-block` | 버튼 |
| `.chip`, `.chip.active` | 칩 (필터/추천 질문) |
| `.msg`, `.msg.bot`, `.msg.user`, `.bubble`, `.tool-chip`, `.msg-actions`, `.typing` | 채팅 |
| `.sheet-handle`, `.sheet-header`, `.sheet-title`, `.sheet-section`, `.section-label` | 바텀시트 내부 |
| `.empty`, `.muted`, `.warn-text` | 유틸 |
| `.search-item` | 검색 결과 행 |
| `.station-row` | 엘리베이터 탭의 역 행 (역명 + 뱃지 + 상태 pill + chevron) |
| `.form-field`, `.form-label`, `.form-row`, `.switch` | 폼 |
