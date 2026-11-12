"""pytest 공통 설정.

코드가 ``AI.src.*`` 절대 경로로 import하므로, AI/ 의 부모 폴더를 import 경로에 추가합니다.
덕분에 AI/ 안에서 ``pytest``를 실행해도, 부모 폴더에서 ``pytest AI/tests``를 실행해도 동작합니다.
"""

import sys
from pathlib import Path

_PARENT = str(Path(__file__).resolve().parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
