"""C5 평가 하네스 검사: C 문항 채점이 돌아가는지, 지표 계산이 결함을 실제로 잡는지."""
import importlib.util
import json
from pathlib import Path

import pytest

from coach.config import MOCK_DIR
from coach.graph import run

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("c_run_eval", ROOT / "eval" / "run_eval.py")
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)
SI = json.loads((MOCK_DIR / "synthetic_stores.json").read_text(encoding="utf-8"))[0]["store_input"]


def test_c_cases_all_graded_and_pass():
    preds = E.run_c_cases()
    assert [p["id"] for p in preds] == ["C01", "C02", "C03", "C04", "C05"]
    assert all(p["correct"] for p in preds)          # 모든 문항에 채점 항목이 있음
    assert all(p["pass"] for p in preds), [(p["id"], p["correct"]) for p in preds if not p["pass"]]


def test_facts_detect_unsupported_sentence_and_constraint_violation():
    out = run("q", SI)
    out["report"]["hypotheses"].append({"text": "주문이 30.0% 줄었어요.", "claim_ids": []})   # 근거 없는 수치 문장
    out["report"]["experiment"]["catalog_id"] = "X06"                                    # 예산 초과·추가 인력 실험
    f = E.c_facts(out)
    assert f["unsupported_sentences"] == 1 and f["constraint_violations"] == 1
    m = E.c_metrics([f])
    assert m["unsupported_sentence_rate"] > 0 and m["constraint_violation_rate"] > 0


def test_regeneration_metric_from_trace():
    f = E.c_facts(run("q", SI, debug_inject="number_tamper"))
    assert f["verify_seq"] == [False, True] and "number_mismatch" in f["first_violation_types"]
    m = E.c_metrics([f, E.c_facts(run("q", SI))])
    assert m["regen_n"] == 1 and m["regen_improved_rate"] == 1.0 and m["regen_resolved_rate"] == 1.0


@pytest.mark.parametrize("inject", ["number_tamper", "banned_phrase"])
def test_injected_defects_never_reach_final_report(inject):
    out = run("q", SI, debug_inject=inject)
    assert out["verify_result"]["passed"] and E.c_facts(out)["unsupported_sentences"] == 0
