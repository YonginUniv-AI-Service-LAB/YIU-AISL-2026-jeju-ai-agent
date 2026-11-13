"""1차 통합 테스트 — 실데이터 시나리오 1개(only_me · 노형동)를 처음부터 끝까지.

실행: COACH_MOCK=0 COACH_LLM=0 python -m pytest tests/test_integration_real.py -q
- COACH_MOCK=1(기본)이면 건너뛴다. 스냅샷이 없으면 만드는 법을 알려 주고 건너뛴다.
- 시나리오·기대값 근거: 노션 A파트 decisions (2026-11-14, 1′·8)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pytest

from coach.config import MOCK_DIR, USE_MOCK

pytestmark = pytest.mark.skipif(USE_MOCK, reason="실데이터 테스트: COACH_MOCK=0 으로 실행")

if not USE_MOCK:
    from coach.contracts import AnalysisResult, Report, VerifyResult
    from coach.data import region_real as R
    from coach.graph import build_graph
    from coach.nodes import a_data as A

CASE_ID = "S-only_me"
EXPECTED = "only_me"            # 사용자 결정(노션 decisions 1′)
QUESTION = "노형동에서 카페 해요. 예산 20만 원, 오전 1명 오후 1명이에요. 손님이 줄었어요."


@pytest.fixture(scope="module")
def snapshot_ok():
    try:
        return R.load_snapshot()["manifest"]
    except FileNotFoundError as e:
        pytest.skip(str(e))


def _run():
    A._real_region.cache_clear()     # 매 실행마다 스냅샷에서 다시 계산(캐시 결과 재사용 방지)
    cases = json.loads((MOCK_DIR / "synthetic_stores_asof_20260831.json").read_text(encoding="utf-8"))
    store = next(c for c in cases if c["case_id"] == CASE_ID)["store_input"]
    return build_graph().invoke({"question": QUESTION, "store_input": store, "trace": []})


@pytest.fixture(scope="module")
def runs(snapshot_ok):
    return _run(), _run()


def test_finishes_without_error(runs):
    out = runs[0]
    # main 의 B.finish: 실제 모드 + LLM 꺼짐 → "fallback". B PR(#1) 머지 후 COACH_LLM=0 이면 "done".
    assert out["status"] in {"done", "fallback"}
    assert out["trace"][-1]["node"] == "B.finish"


def test_same_snapshot_same_result(runs):
    a, b = runs
    for k in ("analysis_result", "report", "verify_result", "status"):
        assert a[k] == b[k], f"{k} 가 두 실행에서 다름"


def test_analysis_result_passes_contract(runs, snapshot_ok):
    ar = AnalysisResult.model_validate(runs[0]["analysis_result"])
    assert ar.snapshot_id == snapshot_ok["snapshot_id"] != "mock00000000"
    assert {"region_visit_prev_pct", "region_visit_yoy_pct", "region_rain_effect_pp",
            "my_orders_change_pct"} <= set(ar.metrics)


def test_verify_passed_or_violations_recorded(runs):
    vr = VerifyResult.model_validate(runs[0]["verify_result"])
    assert vr.passed or vr.violations, "verify 실패인데 위반 사유가 비어 있음"
    assert vr.checked_claims > 0


def test_diagnosis_matches_user_expectation(runs):
    ar = runs[0]["analysis_result"]
    m = ar["metrics"]
    assert ar["diagnosis"] == EXPECTED, (
        f"기대 {EXPECTED} ≠ 실제 {ar['diagnosis']} | 지역 직전 {m['region_visit_prev_pct']}% · "
        f"전년 {m['region_visit_yoy_pct']}% · 내 주문 {m['my_orders_change_pct']}% → config.THRESHOLDS 와 비교해 원인 확인")


def test_report_shows_real_freshness_and_snapshot(runs, snapshot_ok):
    out = runs[0]
    rep = Report.model_validate({k: v for k, v in out["report"].items() if not k.startswith("_")})
    fresh = {f["dataset_no"]: f for f in out["analysis_result"]["data_freshness"]}
    last753 = snapshot_ok["files"]["domestic_753.parquet"]["last_date"]
    assert fresh[753]["last_obs_date"] == last753 and fresh[753]["used"]
    assert fresh[756]["used"] is False                         # 멈춘 데이터는 미사용으로 기록
    assert any(f"No.753" in s and last753 in s for s in rep.sources)
    assert f"[analysis snapshot {snapshot_ok['snapshot_id']}]" in rep.sources
    assert not any("가짜" in f["reason"] for f in fresh.values())   # mock 문구가 남지 않음


def test_recompute_matches_reported_numbers(runs):
    """C의 검증과 같은 길: 보고된 근거 값을 A의 recompute 로 다시 계산하면 같아야 한다."""
    out = runs[0]
    for e in out["analysis_result"]["evidence"]:
        assert A.recompute(e["metric"], out["store_input"]) == pytest.approx(e["value"], abs=0.05), e["claim_id"]
