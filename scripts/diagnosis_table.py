"""S9: 합성 점주 4유형을 그래프로 끝까지 돌려 '기대 진단 vs 실제 진단' 표를 만든다(원인 자동 설명 포함).

사용: COACH_MOCK=0 COACH_LLM=0 python scripts/diagnosis_table.py [--json 저장경로]   (실데이터, 스냅샷 필요)
      COACH_MOCK=1 COACH_LLM=0 python scripts/diagnosis_table.py                    (mock 지역 지표, 비교용)
주의: 정확도는 '합성 사례 N개 기준'이며 실제 점주 대상 정확도가 아니다(CLAUDE.md §5).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from coach.config import MOCK_DIR, THRESHOLDS, USE_MOCK  # noqa: E402
from coach.graph import run  # noqa: E402
from coach.nodes.a_data import reachable_diagnoses  # noqa: E402

T = THRESHOLDS
SRC = "synthetic_stores.json" if USE_MOCK else "synthetic_stores_asof_20260831.json"


def why(my, prev, yoy, rain) -> str:
    """classify() 의 판정 순서를 그대로 따라가며 '어느 조건에서 결정됐는지'를 사람이 읽게 쓴다."""
    if my is None:
        return "내 가게 비교 없음(적합성 판정에서 범위 축소) → 판단 불가"
    if my > T["my_decline_pct"]:
        return f"내 주문 {my}% > {T['my_decline_pct']}% → 뚜렷한 하락 아님"
    if prev <= T["my_decline_pct"] and yoy > T["region_flat_pct"]:
        return f"동네 직전 대비 {prev}% ≤ {T['my_decline_pct']}% 이고 전년 대비 {yoy}% > {T['region_flat_pct']}% → 계절·날씨"
    if yoy <= T["region_flat_pct"] and abs(my - prev) <= T["co_decline_gap_pp"]:
        return f"동네 전년 대비 {yoy}% ≤ {T['region_flat_pct']}% 이고 |내 {my} − 동네 {prev}| ≤ {T['co_decline_gap_pp']}%p → 동반 하락"
    if prev > T["region_flat_pct"]:
        reasons = []
        if prev > T["my_decline_pct"]:
            reasons.append(f"계절·날씨 아님(동네 직전 대비 {prev}%가 하락 아님)")
        if yoy > T["region_flat_pct"]:
            reasons.append(f"동반 아님(동네 전년 대비 {yoy}%가 {T['region_flat_pct']}%보다 높음)")
        elif abs(my - prev) > T["co_decline_gap_pp"]:
            reasons.append(f"동반 아님(내 {my}%와 동네 {prev}% 차이 {abs(my - prev):.1f}%p > {T['co_decline_gap_pp']}%p)")
        return f"동네 직전 대비 {prev}% > {T['region_flat_pct']}% (동네 유지·증가) → 나만 하락. " + " · ".join(reasons)
    return "어느 규칙에도 해당 없음 → 판단 불가"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()
    cases = json.loads((MOCK_DIR / SRC).read_text(encoding="utf-8"))
    rows = []
    for c in cases:
        si = c["store_input"]
        out = run(f"{si.get('dong_name') or ''}에서 카페 해요. 손님이 줄었어요", si)
        ar = out["analysis_result"]
        m = ar["metrics"]
        fit = next((t for t in out["trace"] if t["node"] == "A.check_fitness"), {})
        prev, yoy = m.get("region_visit_prev_pct"), m.get("region_visit_yoy_pct")
        # 평가 가능 여부: 지역 숫자만으로 기대 진단이 '나올 수 있는지' (가게 결과는 보지 않음, 노션 decisions ⑥)
        reach = sorted(reachable_diagnoses(prev, yoy, m.get("region_rain_effect_pp") or 0.0)) if prev is not None else []
        evaluable = c["expected_diagnosis"] in reach or c["expected_diagnosis"] == "undetermined"
        rows.append({
            "case_id": c["case_id"], "dong": si.get("dong_name"), "weeks": len(si.get("weeks") or []),
            "expected": c["expected_diagnosis"], "actual": ar["diagnosis"], "match": ar["diagnosis"] == c["expected_diagnosis"],
            "evaluable": evaluable, "reachable": reach,
            "region_prev_pct": prev, "region_yoy_pct": yoy, "rain_pp": m.get("region_rain_effect_pp"),
            "my_orders_pct": m.get("my_orders_change_pct"), "fitness": fit.get("fitness"), "fitness_cases": fit.get("cases"),
            "why": why(m.get("my_orders_change_pct"), prev, yoy, m.get("region_rain_effect_pp")),
            "snapshot_id": ar["snapshot_id"], "status": out["status"], "verify_passed": out["verify_result"]["passed"],
        })
    ev = [r for r in rows if r["evaluable"]]
    n_ok = sum(r["match"] for r in ev)
    print(f"■ 진단 표 ({'mock 지역 지표' if USE_MOCK else '실데이터'} · snapshot {rows[0]['snapshot_id']}) — 합성 사례 {len(rows)}개 기준")
    print("| 사례 | 읍면동 | 기대 | 실제 | 결과 | 동네 직전 | 동네 전년 | 내 주문 | 판정 | 원인 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        mark = ("✅" if r["match"] else "❌") if r["evaluable"] else f"평가 불가(이 동네·기준일에서 가능: {r['reachable']})"
        print(f"| {r['case_id']} | {r['dong']} | {r['expected']} | {r['actual']} | {mark} | "
              f"{r['region_prev_pct']} | {r['region_yoy_pct']} | {r['my_orders_pct']} | {r['fitness']} {r['fitness_cases']} | {r['why']} |")
    skipped = [r["case_id"] for r in rows if not r["evaluable"]]
    print(f"\n진단 일치: {n_ok}/{len(ev)} — 평가 가능한 합성 사례 {len(ev)}개 기준(실제 점주 대상 정확도 아님)"
          + (f" · 평가 불가 {len(skipped)}개 {skipped}: 기대 유형이 이 기준일 실데이터에 나타날 수 없음" if skipped else ""))
    if a.json:
        Path(a.json).write_text(json.dumps({"mode": "mock" if USE_MOCK else "real", "thresholds": T, "rows": rows},
                                           ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
