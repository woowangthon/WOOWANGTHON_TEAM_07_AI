"""원본 데이터 검증 스크립트.

data/sources.json에 등록된 데이터셋마다 아래를 확인하고 요약을 출력합니다.
  - 파일이 있는지
  - 인코딩 (UTF-8 → CP949 순서로 시도)
  - 헤더가 sources.json의 columns와 같은지
  - 행 수, profile_columns의 값 분포

새 데이터를 받거나 교체한 뒤 실행합니다. 문제가 있으면 종료 코드 1을 반환합니다.

실행:
    python AI/scripts/check_raw_data.py
    python AI/scripts/check_raw_data.py --manifest 다른/sources.json
"""

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "data" / "sources.json"
ENCODINGS = ("utf-8-sig", "cp949")
TOP_VALUES = 8


def read_csv(path: Path):
    """(인코딩, 헤더, 행 목록)을 반환합니다. 어떤 인코딩으로도 못 읽으면 ValueError."""
    for enc in ENCODINGS:
        try:
            with path.open(encoding=enc, newline="") as f:
                rows = list(csv.reader(f))
            if not rows:
                raise ValueError("빈 파일입니다")
            return enc, rows[0], rows[1:]
        except UnicodeDecodeError:
            continue
    raise ValueError(f"인코딩을 판별하지 못했습니다 (시도: {', '.join(ENCODINGS)})")


def check_dataset(ds: dict, base: Path) -> bool:
    print(f"\n[{ds['id']}] {ds['name'] or ds['title']} ({ds['role']})")
    if ds.get("status") == "planned":
        print("  ⏳ 수집 예정 (원본 없음) — 검사 생략")
        return True
    path = base / ds["file"]
    print(f"  파일: {path}")
    if not path.is_file():
        print("  ❌ 파일이 없습니다")
        return False

    try:
        enc, header, rows = read_csv(path)
    except ValueError as e:
        print(f"  ❌ {e}")
        return False

    ok = True
    print(f"  인코딩: {enc} · 데이터 행: {len(rows)}")

    expected = ds["columns"]
    if expected is None:
        print("  ⚠️ sources.json에 columns가 없어 헤더 비교를 건너뜁니다. 실제 헤더:", header)
    elif header != expected:
        ok = False
        missing = [c for c in expected if c not in header]
        extra = [c for c in header if c not in expected]
        print(f"  ❌ 헤더 불일치 — 없는 컬럼: {missing or '-'} / 추가된 컬럼: {extra or '-'}")

    bad = [i + 2 for i, r in enumerate(rows) if len(r) != len(header)]
    if bad:
        ok = False
        print(f"  ❌ 컬럼 수가 다른 행 {len(bad)}개 (예: {bad[:5]}행)")

    for col in ds.get("profile_columns", []):
        if col not in header:
            continue
        idx = header.index(col)
        counts = Counter(r[idx] if idx < len(r) else "" for r in rows)
        top = ", ".join(f"{v or '(빈 값)'}={n}" for v, n in counts.most_common(TOP_VALUES))
        more = f" 외 {len(counts) - TOP_VALUES}종" if len(counts) > TOP_VALUES else ""
        print(f"  {col}: {top}{more}")

    if ok:
        print("  ✅ 정상")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="원본 데이터 검증")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    base = args.manifest.parent
    datasets = manifest["datasets"]
    results = [check_dataset(ds, base) for ds in datasets]

    planned = sum(1 for ds in datasets if ds.get("status") == "planned")
    failed = results.count(False)
    print(f"\n총 {len(datasets)}개: 정상 {len(datasets) - planned - failed}개, 문제 {failed}개, 수집 예정 {planned}개")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
