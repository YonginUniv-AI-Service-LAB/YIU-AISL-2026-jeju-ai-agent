"""각자 자기 노드를 단독 테스트(통합 전 병렬 개발용)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pytest
from coach.config import MOCK_DIR
from coach.nodes import a_data as A, b_orchestrator as B, c_experiment as C

CASES = json.loads((MOCK_DIR / "synthetic_stores.json").read_text(encoding="utf-8"))


# ── A
@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_A_diagnosis_matches_expected(case):
    st = {"store_input": case["store_input"], "trace": []}
    st |= A.check_fitness(st)
    st |= A.analyze(st)
    assert st["analysis_result"]["diagnosis"] == case["expected_diagnosis"]


def test_A_classify_deterministic():
    assert A.classify(-15, -14, -12, 1.5) == A.classify(-15, -14, -12, 1.5)


# ── B
def test_B_extract_and_missing():
    st = B.parse_input({"question": "애월읍 카페, 예산 10만 원, 메뉴 바꿀 수 있어요", "trace": []})
    si = st["store_input"]
    assert si["dong_code"] == "5011025300" and si["budget_krw"] == 100000 and "menu" in si["changeable"]
    assert set(st["missing_slots"]) == {"staff_am", "staff_pm"}


# ── C (가짜 analysis_result 로 단독 실행)
def _fake_state(diag="co_decline"):
    si = CASES[0]["store_input"]
    st = {"store_input": si, "trace": []}
    st |= A.check_fitness(st); st |= A.analyze(st)
    st["analysis_result"]["diagnosis"] = diag
    return st


def test_C_experiment_respects_budget():
    st = _fake_state()
    st |= C.design_experiment(st)
    for e in st["report"]["_experiments"]:
        assert e["cost_krw"] <= st["store_input"]["budget_krw"] and e["extra_staff"] == 0


def test_C_verify_catches_banned_and_tampered_number():
    st = _fake_state()
    st |= C.design_experiment(st); st |= C.compose_report(st)
    assert C.verify(st)["verify_result"]["passed"]
    st["analysis_result"]["evidence"][0]["value"] = 99.9  # 수치 조작
    st["report"]["hypotheses"].append({"text": "원인은 경쟁점 때문입니다.", "claim_ids": []})
    types = {v["type"] for v in C.verify(st)["verify_result"]["violations"]}
    assert {"number_mismatch", "banned_phrase"} <= types
