"""스냅샷 검증 게이트 실행: 결과 표를 출력하고, FAIL 이 있으면 종료 코드 1 로 끝난다.

사용: python tasks.py validate [--as-of YYYY-MM-DD]
      python scripts/validate_snapshot.py [--as-of YYYY-MM-DD] [--json 저장경로]
검사 내용·허용치: src/coach/data/validate.py
"""
import argparse
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

ap = argparse.ArgumentParser()
ap.add_argument("--as-of", default=os.getenv("COACH_AS_OF"))
ap.add_argument("--json", help="검사 결과를 JSON 으로 저장할 경로")
a = ap.parse_args()
if a.as_of:
    os.environ["COACH_AS_OF"] = a.as_of   # region_real 이 읽기 전에 설정

from coach.data import region_real as R  # noqa: E402
from coach.data import validate as V  # noqa: E402

try:
    checks = V.run_all(R.AS_OF)
except FileNotFoundError as e:
    print(f"FAIL {e}")
    sys.exit(1)

print(f"■ 스냅샷 검증 (AS_OF {R.AS_OF})")
for c in checks:
    print(f"  [{c.status:4}] {c.no}. {c.name}: {c.detail}")
lag_today = (date.today() - R.AS_OF).days
print(f"  (기록) 오늘 기준 AS_OF 경과 {lag_today}일 — 판정에는 쓰지 않음")
code = V.exit_code(checks)
print("→ 통과" if code == 0 else "→ 실패: 이 스냅샷으로 분석하지 마세요")
if a.json:
    Path(a.json).write_text(V.to_json(checks, R.AS_OF), encoding="utf-8")
sys.exit(code)
