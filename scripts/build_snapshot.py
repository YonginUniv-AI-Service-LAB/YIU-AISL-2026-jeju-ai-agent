"""S2-2: data/raw/fetch/<as_of>/ 의 API 원본(JSON) → data/snapshot/<as_of>/ parquet + manifest.json

원칙
- 스냅샷에는 원본 값을 그대로(타입만 정리) 담는다. 결측 처리·합계 등 해석 규칙은 region_real 에 둔다.
- 파일별 sha256 → manifest.json. snapshot_id = (파일명:해시 목록)의 sha256 앞 12자리.
- 변환·저장은 라이브 모드와 같은 코드(src/coach/data/snapshot_io.py).

사용: python tasks.py snapshot --as-of 2026-08-31   (fetch 다음에 자동 실행)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from coach.data.region_real import AS_OF as _AS_OF  # noqa: E402  (기준일은 region_real 한 곳에서)
from coach.data.snapshot_io import domestic_df, foreign_df, weather_df, write_snapshot  # noqa: E402

AS_OF = str(_AS_OF)
SRC = ROOT / "data" / "raw" / "fetch" / AS_OF.replace("-", "")
OUT = ROOT / "data" / "snapshot" / AS_OF


def _pages(sub: str) -> list[dict]:
    rows = []
    for p in sorted((SRC / sub).rglob("p*.json")):
        rows += json.loads(p.read_text(encoding="utf-8"))["response"]["data"] or []
    return rows


def _asos_items() -> list[dict]:
    items = []
    for p in sorted((SRC / "asos").glob("*.json")):
        it = json.loads(p.read_text(encoding="utf-8"))["response"]["response"]["body"]["items"]["item"]
        items += it if isinstance(it, list) else [it]
    return items


def main() -> None:
    tables = {"domestic": domestic_df(_pages("753")), "foreign": foreign_df(_pages("748")), "weather": weather_df(_asos_items())}
    man = write_snapshot(OUT, tables, AS_OF)
    for n, f in man["files"].items():
        print(f"{n}: {f['rows']}행 {f['first_date']}~{f['last_date']} sha256={f['sha256'][:12]}…")
    print("snapshot_id =", man["snapshot_id"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
