"""/api/chat 개발 서버 실행.

    python AI/scripts/run_server.py              # http://127.0.0.1:8001
    python AI/scripts/run_server.py --port 9000 --reload

LLM 키(AI/.env)가 없으면 모든 요청이 폴백 엔진으로 응답합니다 (metadata.fallback_reason="not_configured").
BE 데이터로 실행: AI/.env에 AI_DATA_SOURCE=be, BE_API_BASE_URL=http://127.0.0.1:8000 (BE 서버를 먼저 띄움)
"""

import argparse
import sys
from pathlib import Path

# AI/ 의 부모 폴더를 import 경로에 추가 (AI.src 패키지 import용)
PARENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PARENT))


def _warm_up_data_source() -> None:
    """BE 데이터 소스면 역 목록을 미리 읽어 첫 요청이 느려지지 않게 합니다 (--reload 없이 실행할 때 효과)."""
    import os
    if os.getenv("AI_DATA_SOURCE", "mock").strip().lower() != "be":
        return
    from AI.src.data import DataUnavailableError, get_data_source
    try:
        print(f"BE 역 목록 {len(get_data_source().stations())}개를 읽었습니다.")
    except DataUnavailableError as e:
        print(f"경고: BE에 연결하지 못했습니다 ({e}). 요청마다 다시 시도하고, 실패하면 '확인할 수 없음'으로 안내합니다.")


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="우왕이 AI /api/chat 개발 서버")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    _warm_up_data_source()
    uvicorn.run("AI.src.api.app:app", host=args.host, port=args.port, reload=args.reload,
                app_dir=str(PARENT))


if __name__ == "__main__":
    main()
