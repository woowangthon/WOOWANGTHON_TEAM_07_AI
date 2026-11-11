"""수도권 지하철 및 엘리베이터 Mock 데이터.

FE/Prototype-v2/js/mock-data.js의 1~8호선 체계를 기반으로 구성되었습니다.
"""

from typing import Dict, List, Any

# 1~8호선 노선 정보
LINES: Dict[str, dict] = {
    "1": {"id": "1", "name": "1호선", "short": "1", "color": "#0052A4"},
    "2": {"id": "2", "name": "2호선", "short": "2", "color": "#00A84D"},
    "3": {"id": "3", "name": "3호선", "short": "3", "color": "#EF7C1C"},
    "4": {"id": "4", "name": "4호선", "short": "4", "color": "#00A5DE"},
    "5": {"id": "5", "name": "5호선", "short": "5", "color": "#996CAC"},
    "6": {"id": "6", "name": "6호선", "short": "6", "color": "#CD7C2F"},
    "7": {"id": "7", "name": "7호선", "short": "7", "color": "#747F00"},
    "8": {"id": "8", "name": "8호선", "short": "8", "color": "#E6186C"},
}

# 2호선 순환선 본선 역 순서 (시청 -> 시계방향 -> 충정로 -> 시청)
LINE_2_LOOP: List[str] = [
    "cityhall", "euljiro1ga", "euljiro3ga", "euljiro4ga", "ddp", "sindang",
    "sangwangsimni", "wangsimni", "hanyang_univ", "ttukseom", "seongsu", "konkuk",
    "guui", "gangbyeon", "jamsilnaru", "jamsil", "jamsilsaenae", "sports_complex",
    "samseong", "seolleung", "yeoksam", "gangnam", "gyodae", "seocho", "bangbae",
    "sadang", "nakseongdae", "seoul_univ", "bongcheon", "sillim", "sindaebang",
    "guro_digital", "daerim", "sindorim", "mullae", "yeongdeungpo_gu", "dangsan",
    "hapjeong", "hongik", "sinchon", "ewha", "ahyeon", "chungjeongno"
]

# 주요 역 목록 (1~8호선)
STATIONS: Dict[str, dict] = {
    "cityhall": {"id": "cityhall", "name": "시청", "lines": ["1", "2"]},
    "euljiro1ga": {"id": "euljiro1ga", "name": "을지로입구", "lines": ["2"]},
    "euljiro3ga": {"id": "euljiro3ga", "name": "을지로3가", "lines": ["2", "3"]},
    "euljiro4ga": {"id": "euljiro4ga", "name": "을지로4가", "lines": ["2", "5"]},
    "ddp": {"id": "ddp", "name": "동대문역사문화공원", "lines": ["2", "4", "5"]},
    "sindang": {"id": "sindang", "name": "신당", "lines": ["2", "6"]},
    "wangsimni": {"id": "wangsimni", "name": "왕십리", "lines": ["2", "5"]},
    "konkuk": {"id": "konkuk", "name": "건대입구", "lines": ["2", "7"]},
    "jamsil": {"id": "jamsil", "name": "잠실", "lines": ["2", "8"]},
    "sports_complex": {"id": "sports_complex", "name": "종합운동장", "lines": ["2"]},
    "samseong": {"id": "samseong", "name": "삼성", "lines": ["2"]},
    "seolleung": {"id": "seolleung", "name": "선릉", "lines": ["2"]},
    "yeoksam": {"id": "yeoksam", "name": "역삼", "lines": ["2"]},
    "gangnam": {"id": "gangnam", "name": "강남", "lines": ["2"]},
    "gyodae": {"id": "gyodae", "name": "교대", "lines": ["2", "3"]},
    "seocho": {"id": "seocho", "name": "서초", "lines": ["2"]},
    "sadang": {"id": "sadang", "name": "사당", "lines": ["2", "4"]},
    "seoul_univ": {"id": "seoul_univ", "name": "서울대입구", "lines": ["2"]},
    "sillim": {"id": "sillim", "name": "신림", "lines": ["2"]},
    "guro_digital": {"id": "guro_digital", "name": "구로디지털단지", "lines": ["2"]},
    "daerim": {"id": "daerim", "name": "대림", "lines": ["2", "7"]},
    "sindorim": {"id": "sindorim", "name": "신도림", "lines": ["1", "2"]},
    "yeongdeungpo_gu": {"id": "yeongdeungpo_gu", "name": "영등포구청", "lines": ["2", "5"]},
    "dangsan": {"id": "dangsan", "name": "당산", "lines": ["2"]},
    "hapjeong": {"id": "hapjeong", "name": "합정", "lines": ["2", "6"]},
    "hongik": {"id": "hongik", "name": "홍대입구", "lines": ["2"]},
    "sinchon": {"id": "sinchon", "name": "신촌", "lines": ["2"]},
    "seoul": {"id": "seoul", "name": "서울역", "lines": ["1", "4"]},
    "jongno3ga": {"id": "jongno3ga", "name": "종로3가", "lines": ["1", "3", "5"]},
    "express_bus": {"id": "express_bus", "name": "고속터미널", "lines": ["3", "7"]},
}

# 별칭 및 축약어 사전
ALIASES: Dict[str, str] = {
    "동역사": "ddp",
    "동대문운동장": "ddp",
    "동대문역사문화공원": "ddp",
    "홍대": "hongik",
    "홍대입구": "hongik",
    "건대": "konkuk",
    "건대입구": "konkuk",
    "서울대": "seoul_univ",
    "서울대입구": "seoul_univ",
    "구디": "guro_digital",
    "구로디지털": "guro_digital",
    "구로디지털단지": "guro_digital",
    "영등포구청": "yeongdeungpo_gu",
    "고터": "express_bus",
    "고속터미널": "express_bus",
    "종3": "jongno3ga",
    "종로3가": "jongno3ga",
    "서울역": "seoul",
}

# 엘리베이터 상태 데이터
ELEVATORS: Dict[str, List[dict]] = {
    "gangnam": [
        {"segment_id": "gangnam-e1", "type": "exit", "location": "1번 출구", "floors": "지상 ↔ B1 대합실", "status": "AVAILABLE", "reason": ""},
        {"segment_id": "gangnam-e2", "type": "exit", "location": "12번 출구", "floors": "지상 ↔ B1 대합실", "status": "AVAILABLE", "reason": ""},
        {"segment_id": "gangnam-e3", "type": "platform", "location": "2호선 승강장 (내선)", "floors": "B1 대합실 ↔ B2 승강장", "status": "AVAILABLE", "reason": ""},
        {"segment_id": "gangnam-e4", "type": "platform", "location": "2호선 승강장 (외선)", "floors": "B1 대합실 ↔ B2 승강장", "status": "AVAILABLE", "reason": ""},
    ],
    "cityhall": [
        {"segment_id": "cityhall-e1", "type": "exit", "location": "1번 출구", "floors": "지상 ↔ B1 대합실", "status": "AVAILABLE", "reason": ""},
        {"segment_id": "cityhall-e2", "type": "exit", "location": "9번 출구", "floors": "지상 ↔ B1 대합실", "status": "AVAILABLE", "reason": ""},
        {"segment_id": "cityhall-e3", "type": "platform", "location": "2호선 승강장", "floors": "B1 ↔ B2", "status": "AVAILABLE", "reason": ""},
    ],
    "yeoksam": [
        {"segment_id": "yeoksam-e1", "type": "exit", "location": "3번 출구", "floors": "지상 ↔ B1 대합실", "status": "AVAILABLE", "reason": ""},
        {"segment_id": "yeoksam-e2", "type": "exit", "location": "7번 출구", "floors": "지상 ↔ B1 대합실", "status": "AVAILABLE", "reason": ""},
        {"segment_id": "yeoksam-e3", "type": "platform", "location": "2호선 승강장", "floors": "B1 대합실 ↔ B2 승강장", "status": "AVAILABLE", "reason": ""},
    ],
    "euljiro1ga": [
        {"segment_id": "euljiro1ga-e1", "type": "exit", "location": "1번 출구", "floors": "지상 ↔ B1 대합실", "status": "AVAILABLE", "reason": ""},
        {"segment_id": "euljiro1ga-e2", "type": "platform", "location": "2호선 승강장", "floors": "B1 대합실 ↔ B2 승강장", "status": "AVAILABLE", "reason": ""},
    ],
    "sadang": [
        {"segment_id": "sadang-e1", "type": "exit", "location": "6번 출구", "floors": "지상 ↔ B1 대합실", "status": "AVAILABLE", "reason": ""},
        {"segment_id": "sadang-e2", "type": "exit", "location": "10번 출구", "floors": "지상 ↔ B1 대합실", "status": "AVAILABLE", "reason": ""},
        {"segment_id": "sadang-e3", "type": "platform", "location": "2호선 승강장", "floors": "B1 대합실 ↔ B2 승강장", "status": "UNAVAILABLE", "reason": "출입문 센서 이상 — 긴급 수리 중"},
        {"segment_id": "sadang-e4", "type": "transfer", "location": "4호선 환승통로", "floors": "B2 ↔ B4 4호선 승강장", "status": "AVAILABLE", "reason": ""},
    ],
    "sillim": [
        {"segment_id": "sillim-e1", "type": "exit", "location": "6번 출구", "floors": "지상 ↔ B1 대합실", "status": "UNAVAILABLE", "reason": "침수 우려로 운행 중지 — 배수 작업 중"},
        {"segment_id": "sillim-e2", "type": "exit", "location": "9번 출구", "floors": "지상 ↔ B1 대합실", "status": "AVAILABLE", "reason": ""},
        {"segment_id": "sillim-e3", "type": "platform", "location": "2호선 승강장", "floors": "B1 대합실 ↔ B2 승강장", "status": "AVAILABLE", "reason": ""},
    ],
    "sindorim": [
        {"segment_id": "sindorim-e1", "type": "exit", "location": "1번 출구", "floors": "지상 ↔ B1 대합실", "status": "AVAILABLE", "reason": ""},
        {"segment_id": "sindorim-e2", "type": "exit", "location": "3번 출구", "floors": "지상 ↔ 2F 대합실", "status": "UNAVAILABLE", "reason": "구동 모터 고장 — 부품 교체 중 (복구 예정 18:00)"},
        {"segment_id": "sindorim-e3", "type": "platform", "location": "1호선 승강장", "floors": "1F ↔ 지상 승강장", "status": "AVAILABLE", "reason": ""},
        {"segment_id": "sindorim-e4", "type": "platform", "location": "2호선 본선 승강장", "floors": "B1 대합실 ↔ B2 승강장", "status": "AVAILABLE", "reason": ""},
        {"segment_id": "sindorim-e5", "type": "transfer", "location": "1·2호선 환승통로", "floors": "지상 ↔ B1", "status": "AVAILABLE", "reason": ""},
    ],
    "jamsil": [
        {"segment_id": "jamsil-e1", "type": "exit", "location": "1번 출구", "floors": "지상 ↔ B1 대합실", "status": "AVAILABLE", "reason": ""},
        {"segment_id": "jamsil-e2", "type": "platform", "location": "2호선 승강장", "floors": "B1 대합실 ↔ B2 승강장", "status": "AVAILABLE", "reason": ""},
    ],
    "hongik": [
        {"segment_id": "hongik-e1", "type": "exit", "location": "2번 출구", "floors": "지상 ↔ B1 대합실", "status": "AVAILABLE", "reason": ""},
        {"segment_id": "hongik-e2", "type": "platform", "location": "2호선 승강장", "floors": "B1 대합실 ↔ B2 승강장", "status": "AVAILABLE", "reason": ""},
    ],
}

# 대체역 우회 안내 팁
ALTERNATIVE_TIPS: Dict[str, str] = {
    "gangnam>yeoksam": "역삼역 3번 출구로 나와 테헤란로를 따라 강남역 방향으로 도보 약 10분 (800m). 146·341번 버스로 1정거장.",
    "cityhall>euljiro1ga": "을지로입구역 1번 출구로 나와 시청광장 방면으로 평지 도보 약 6분 (450m).",
    "sadang>bangbae": "방배역 3번 출구 엘리베이터 이용 권장. 사당역 방면 버스(서초17번) 환승 가능.",
    "sindorim>mullae": "문래역 6번 출구 엘리베이터 이용 권장. 도보 약 12분 (900m).",
    "sillim>sindaebang": "신대방역 0번 출구 엘리베이터 이용 권장.",
}

# 기본 운행 시간표 파라미터 (단위: 분)
TIMING_PARAMS = {
    "minutes_per_stop": 2,       # 역간 열차 이동 시간
    "headway_line2": 3,          # 2호선 배차 간격
    "default_headway": 5,        # 타 노선 기본 배차
    "home_walk": 7,              # 출발지 -> 역 도보
    "station_entry_elevator": 3, # 역사 진입 및 엘리베이터 탑승
    "exit_elevator": 3,          # 도착역 승강장 -> 출구
    "safety_margin": 2,          # 교통약자 여유 시간
}

