"""데이터 소스.

    source.py       DataSource 인터페이스, get_data_source / use_data_source
    mock_source.py  MockDataSource (mock_data.py를 감싼 구현)
    mock_data.py    Mock 상수 (역·별칭·엘리베이터·대체역·시간)

도구와 폴백 엔진은 mock_data.py를 직접 import하지 않고 get_data_source()를 씁니다.
BE API 연동 시 이 패키지에 BE 구현을 추가합니다 (이슈 #4).
"""

from .source import DataSource, get_data_source, set_data_source, station_name, use_data_source

__all__ = ["DataSource", "get_data_source", "set_data_source", "use_data_source", "station_name"]
