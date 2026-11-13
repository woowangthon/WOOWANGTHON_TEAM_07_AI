"""/api/chat 개발 서버 실행.

    python AI/scripts/run_server.py              # http://127.0.0.1:8001
    python AI/scripts/run_server.py --port 9000 --reload

LLM 키(AI/.env)가 없으면 모든 요청이 폴백 엔진으로 응답합니다 (metadata.fallback_reason="not_configured").
"""

import argparse
import sys
from pathlib import Path

# AI/ 의 부모 폴더를 import 경로에 추가 (AI.src 패키지 import용)
PARENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PARENT))


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="우왕이 AI /api/chat 개발 서버")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run("AI.src.api.app:app", host=args.host, port=args.port, reload=args.reload,
                app_dir=str(PARENT))


if __name__ == "__main__":
    main()
