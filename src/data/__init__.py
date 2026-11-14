"""데이터 소스.

    source.py       DataSource 인터페이스, get_data_source / use_data_source
    mock_source.py  MockDataSource (mock_data.py를 감싼 구현)
    mock_data.py    Mock 상수 (역·별칭·엘리베이터·대체역·시간)
    be_source.py    BeDataSource (BE API 호출, AI_DATA_SOURCE=be. 이슈 #4·#5)

도구와 폴백 엔진은 mock_data.py를 직접 import하지 않고 get_data_source()를 씁니다.
"""

from .source import (
    DataSource, DataUnavailableError, get_data_source, set_data_source, station_name, use_data_source,
)

__all__ = ["DataSource", "DataUnavailableError", "get_data_source", "set_data_source", "use_data_source", "station_name"]
