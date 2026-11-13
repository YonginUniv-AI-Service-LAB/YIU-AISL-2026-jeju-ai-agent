"""합성 점주 4유형 + 되묻기 + 검증 루프 시연.  python scripts/run_demo.py [case_id]"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from coach.config import MOCK_DIR
from coach.graph import run

cases = json.loads((MOCK_DIR / "synthetic_stores.json").read_text(encoding="utf-8"))
want = sys.argv[1] if len(sys.argv) > 1 else None
for c in cases:
    if want and c["case_id"] != want:
        continue
    out = run("최근 손님이 줄었는데 뭘 해보면 좋을까요?", c["store_input"])
    r = out["report"]
    print(f"\n=== {c['case_id']}  기대={c['expected_diagnosis']}  결과={r['diagnosis']}  status={out['status']}")
    for sec in ["observed", "my_store", "hypotheses"]:
        for s in r[sec]:
            print(f"  [{sec}] {s['text']}  {s['claim_ids']}")
    if r.get("experiment"):
        e = r["experiment"]; print(f"  [실험] {e['catalog_id']} {e['title']} / 비용 {e['cost_krw']:,}원 / 중단: {e['stop_rule']}")
    print("  trace:", " → ".join(t["node"] for t in out["trace"]))

if not want:
    print("\n=== 되묻기 시연")
    out = run("애월에서 카페해요. 손님이 줄었어요")
    print(" ", out["status"], "|", out["ask_back"])
    print("\n=== 검증 루프 시연(금지 표현 주입)")
    out = run("뭘 해볼까요", cases[0]["store_input"], debug_inject="banned_phrase")
    print(" ", out["status"], "| trace:", " → ".join(t["node"] for t in out["trace"]))
