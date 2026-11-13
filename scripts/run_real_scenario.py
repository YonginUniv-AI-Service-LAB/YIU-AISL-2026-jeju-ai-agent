"""실데이터 시나리오 1개를 돌려 사람이 읽는 형태로 출력한다(1차 통합 테스트용).

사용: COACH_MOCK=0 COACH_LLM=0 python scripts/run_real_scenario.py [case_id]   (기본 S-only_me)
필요: data/snapshot/2026-08-31/ (scripts/fetch_snapshot.py → scripts/build_snapshot.py)
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8")
if os.getenv("COACH_MOCK", "1") != "0":
    sys.exit("COACH_MOCK=0 으로 실행하세요(실데이터 모드).")

from coach.config import MOCK_DIR  # noqa: E402
from coach.graph import build_graph  # noqa: E402

case_id = sys.argv[1] if len(sys.argv) > 1 else "S-only_me"
cases = {c["case_id"]: c for c in json.loads((MOCK_DIR / "synthetic_stores_asof_20260831.json").read_text(encoding="utf-8"))}
case = cases[case_id]
si = case["store_input"]
q = f"{si.get('dong_name') or ''}에서 카페 해요. 예산 {si['budget_krw'] // 10000}만 원, 오전 {si['staff_am']}명 오후 {si['staff_pm']}명."

out = build_graph().invoke({"question": q, "store_input": si, "trace": []})
ar, rep, vr = out["analysis_result"], out["report"], out.get("verify_result") or {}

print(f"■ 시나리오 {case_id} (기대 {case['expected_diagnosis']}) — 합성 점주 + 실제 지역 지표")
print(f"  질문: {q}")
print(f"  상태: {out['status']} | 진단: {ar['diagnosis']} (근거 강도 {ar['evidence_strength']}) | snapshot {ar['snapshot_id']}")
print("\n■ 근거")
for e in ar["evidence"]:
    print(f"  {e['claim_id']} {e['metric']} = {e['value']} {e['unit']}  [{e['period']}] 출처 {e['source_nos']}")
print("\n■ 데이터 적합성")
for f in ar["data_freshness"]:
    print(f"  No.{f['dataset_no']} {f['name']}: 마지막 관측 {f['last_obs_date']} · {'사용' if f['used'] else '미사용'} · {f['reason']}")
for n in ar["notes"]:
    print(f"  비고: {n}")
print("\n■ 리포트")
for sec in ("observed", "my_store", "hypotheses", "to_check", "limits"):
    for s in rep.get(sec, []):
        print(f"  [{sec}] {s['text']}")
if rep.get("experiment"):
    x = rep["experiment"]
    print(f"  [experiment] {x['title']} (비용 {x['cost_krw']:,}원)")
print("  [sources]", " / ".join(rep["sources"]))
print(f"\n■ 검증: {'통과' if vr.get('passed') else '위반'} · 재계산한 근거 {vr.get('checked_claims')}개")
for v in vr.get("violations", []):
    print(f"  - {v['type']}: {v['reason']}")
