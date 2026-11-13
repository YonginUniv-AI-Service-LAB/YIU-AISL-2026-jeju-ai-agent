"""합성 점주 실적 주차를 실제 모드 기준일에 맞춘 사본을 만든다(원본 synthetic_stores.json 은 그대로).

노션 decisions 2-1: 기준일 2026-08-31 → 마지막 주가 2026-08-24(월)~08-30(일)이 되도록 앞당김.
주문·매출 값은 바꾸지 않고 week_start 만 옮긴다(합성 데이터임은 is_synthetic=true 로 유지).

사용: python scripts/shift_synthetic_weeks.py
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from coach.config import MOCK_DIR  # noqa: E402
from coach.data.region_real import RECENT_END  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
SRC = MOCK_DIR / "synthetic_stores.json"
DST = MOCK_DIR / f"synthetic_stores_asof_{RECENT_END + timedelta(days=1):%Y%m%d}.json"

cases = json.loads(SRC.read_text(encoding="utf-8"))
last = max(date.fromisoformat(w["week_start"]) for c in cases for w in c["store_input"].get("weeks", []))
shift = (RECENT_END - timedelta(days=6)) - last          # 마지막 주 시작을 RECENT_END 주의 월요일로
for c in cases:
    for w in c["store_input"].get("weeks", []):
        w["week_start"] = str(date.fromisoformat(w["week_start"]) + shift)
    c["_note"] = f"실제 모드용 사본: 원본 주차를 {shift.days}일 이동(값 동일). 원본은 synthetic_stores.json"
DST.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"{DST.name}: {shift.days}일 이동, 마지막 주 {last + shift}")
