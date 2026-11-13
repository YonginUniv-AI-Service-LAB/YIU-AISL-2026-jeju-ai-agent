import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from coach.config import MOCK_DIR
from coach.contracts import Report
from coach.graph import run

CASES = json.loads((MOCK_DIR / "synthetic_stores.json").read_text(encoding="utf-8"))


def test_end_to_end_all_cases():
    for c in CASES:
        out = run("손님이 줄었어요", c["store_input"])
        assert out["status"] == "done"
        Report.model_validate({k: v for k, v in out["report"].items() if not k.startswith("_")})
        assert out["report"]["diagnosis"] == c["expected_diagnosis"]


def test_ask_back():
    out = run("애월읍에서 카페 해요")
    assert out["status"] == "need_input" and "예산" in out["ask_back"]


def test_retry_loop_recovers():
    out = run("q", CASES[0]["store_input"], debug_inject="banned_phrase")
    nodes = [t["node"] for t in out["trace"]]
    assert nodes.count("C.compose_report") == 2 and out["status"] == "done"


def test_revisit_review():
    out = run("2주 해봤어요", CASES[0]["store_input"], previous_experiment={"catalog_id": "X01"})
    assert out["experiment_review"]["verdict"] in {"개선 신호", "악화 신호", "확인 불가"}


def test_deterministic():
    a = run("q", CASES[1]["store_input"])["analysis_result"]
    b = run("q", CASES[1]["store_input"])["analysis_result"]
    assert a == b
