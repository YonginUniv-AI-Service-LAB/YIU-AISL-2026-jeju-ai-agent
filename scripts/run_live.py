"""라이브 모드 실행: 점주 주문 기록 시기에 맞춰 동네 흐름을 API로 불러와 분석한다(네트워크·키 필요).

사용: python tasks.py live [--case S-only_me] [--shift-weeks -9]
      --shift-weeks: 합성 점주 주문 기록을 N주 옮겨 '다른 시기' 질문을 만든다(예: -9 → 6월 말까지의 기록)
처음 조회는 API가 불안정해 1~3분 걸릴 수 있고, 같은 요청은 캐시(data/live/cache)로 즉시 응답한다.
조회한 데이터는 data/live/snapshots/<읍면동>_<창 끝>/ 에 manifest 와 함께 '얼려' 저장된다(나중에 재현용).
"""
import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("COACH_MOCK", "0")
os.environ.setdefault("COACH_LIVE", "1")

from coach.config import MOCK_DIR  # noqa: E402
from coach.data import api_client  # noqa: E402
from coach.graph import run  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--case", default="S-only_me")
ap.add_argument("--shift-weeks", type=int, default=0)
a = ap.parse_args()

case = next(c for c in json.loads((MOCK_DIR / "synthetic_stores_asof_20260831.json").read_text(encoding="utf-8"))
            if c["case_id"] == a.case)
si = json.loads(json.dumps(case["store_input"]))
for w in si.get("weeks", []):
    w["week_start"] = str(date.fromisoformat(w["week_start"]) + timedelta(weeks=a.shift_weeks))
weeks = si.get("weeks") or []
q = f"{si.get('dong_name') or ''}에서 카페 해요. 손님이 줄었어요"
print(f"■ 라이브 분석 {a.case} · 주문 기록 {weeks[0]['week_start'] if weeks else '-'} ~ {weeks[-1]['week_start'] if weeks else '-'} 주")

t0 = time.perf_counter()
out = run(q, si)
dt = time.perf_counter() - t0
ar = out["analysis_result"]
fit = next((t for t in out["trace"] if t["node"] == "A.check_fitness"), {})
print(f"  처리 {dt:.1f}s · API 호출 {api_client.STATS['calls']}회(재시도 {api_client.STATS['retries']}) · "
      f"지역 데이터 출처: {fit.get('region_source')} · snapshot {ar['snapshot_id']}")
print(f"  live: {fit.get('live')}")
print(f"  판정: {fit.get('fitness')} {fit.get('cases')} · mode {ar['mode']} · 진단 {ar['diagnosis']} · status {out['status']}")
for e in ar["evidence"]:
    print(f"  {e['claim_id']} {e['metric']} = {e['value']} [{e['period']}] 출처 {e['source_nos']}")
for n in ar["notes"]:
    print(f"  비고: {n}")
print("  [sources]", " / ".join(out["report"]["sources"]))
print(f"  검증: {'통과' if out['verify_result']['passed'] else '위반'}")
