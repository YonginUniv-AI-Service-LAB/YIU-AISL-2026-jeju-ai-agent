"""B 조건 추출 평가 — 질문 이해(parse_input)만 따로 채점.

사용:  python eval/run_eval_b.py          (규칙 기반 경로)
       python eval/run_eval_b.py --llm    (LLM 경로, .env 에 LLM_API_KEY 필요)

지표
- 항목 정확도: 기대한 항목(지역·예산·인력·바꿀 수 있는 항목) 중 맞게 뽑은 비율
- 되묻기 정확도: 빠진 항목(되물어야 할 것)을 정확히 찾은 문항 비율
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from coach.nodes import b_orchestrator as B

use_llm = "--llm" in sys.argv
cases = [json.loads(l) for l in (ROOT / "eval" / "cases_b.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]

slot_ok = slot_total = miss_ok = 0
print(f"경로: {'LLM' if use_llm else '규칙 기반'}\n")
for c in cases:
    st = B.parse_input({"question": c["question"], "trace": [], "llm_available": use_llm})
    got, exp = st["store_input"], c["expect"]
    wrong = []
    for k, v in exp["slots"].items():
        g = got.get(k)
        same = sorted(g or []) == sorted(v) if isinstance(v, list) else g == v
        slot_ok += same
        slot_total += 1
        if not same:
            wrong.append(f"{k}: 기대 {v} / 결과 {g}")
    m_ok = sorted(st["missing_slots"]) == sorted(exp["missing"])
    miss_ok += m_ok
    if not m_ok:
        wrong.append(f"되묻기: 기대 {exp['missing']} / 결과 {st['missing_slots']}")
    if use_llm and not st["llm_available"]:
        wrong.append(f"LLM 실패 → 규칙 기반으로 대체됨 ({st['trace'][-1].get('llm_error')})")
    print(f"{'✅' if not wrong else '❌'} {c['id']} {c['point']}")
    for w in wrong:
        print("     -", w)

print(f"\n항목 정확도  {slot_ok}/{slot_total} ({slot_ok / slot_total:.0%})")
print(f"되묻기 정확도 {miss_ok}/{len(cases)} ({miss_ok / len(cases):.0%})")