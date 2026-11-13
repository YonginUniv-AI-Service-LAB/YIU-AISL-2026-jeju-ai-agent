"""★C 평가 하네스 초안: 노드별 지표 집계.  python eval/run_eval.py"""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from coach.config import MOCK_DIR
from coach.graph import run

stores = {c["case_id"]: c["store_input"] for c in json.loads((MOCK_DIR / "synthetic_stores.json").read_text(encoding="utf-8"))}
rows = []
for line in (ROOT / "eval" / "cases.sample.jsonl").read_text(encoding="utf-8").splitlines():
    case = json.loads(line)
    out = run(case["question"], stores.get(case["store_case"]) if case["store_case"] else {})
    exp, ok = case["expect"], True
    if "status" in exp: ok &= out.get("status") == exp["status"]
    if "diagnosis" in exp: ok &= out.get("report", {}).get("diagnosis") == exp["diagnosis"]
    text = json.dumps(out.get("report", {}), ensure_ascii=False)
    for p in exp.get("must_not_contain", []): ok &= p not in text
    rows.append({"id": case["id"], "owner": case["owner"], "pass": bool(ok), "status": out.get("status")})
for r in rows: print(r)
print(f"pass {sum(r['pass'] for r in rows)}/{len(rows)}")
(ROOT / "eval" / "last_result.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
