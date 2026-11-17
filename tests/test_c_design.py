"""C2 실험 설계·랭킹 검사: 조건 위반 0, 결정론, 순위 규칙, region_only, 검증 연동."""
import itertools
import json

import pytest

from coach.config import MOCK_DIR
from coach.contracts import Report
from coach.nodes import c_experiment as C

DIAGS = ["co_decline", "only_me", "season_weather"]
CAT = C._catalog()


def _ar(diag="co_decline", mode="current"):
    return {"mode": mode, "snapshot_id": "test", "data_freshness": [], "metrics": {}, "diagnosis": diag,
            "evidence_strength": "중", "evidence": [], "notes": []}


def _si(**kw):
    return dict(budget_krw=300_000, staff_am=1, staff_pm=2, changeable=["menu", "channel", "price"]) | kw


def _design(si, ar):
    return C.design_experiment({"store_input": si, "analysis_result": ar, "trace": []})


def _exps(si, ar):
    return _design(si, ar)["report"]["_experiments"]


# ───────────── 조건 위반 0 (조건 조합 전체)
GRID = [_si(budget_krw=b, staff_am=am, staff_pm=pm, changeable=list(ch))
        for b in (0, 50_000, 300_000, 1_000_000)
        for am, pm in ((1, 1), (1, 2), (2, 2))
        for n in range(5) for ch in itertools.combinations(["menu", "hours", "price", "channel"], n)]


@pytest.mark.parametrize("diag", DIAGS)
def test_no_constraint_violation_over_condition_grid(diag):
    from coach.contracts import StoreInput
    by_id = {x["id"]: x for x in CAT}
    for si in GRID:
        exps = _exps(si, _ar(diag))
        assert len(exps) <= 3
        for e in exps:
            item = by_id[e["catalog_id"]]
            assert diag in item["fits"]
            assert C.infeasible_reasons(item, StoreInput.model_validate(si)) == [], (si, e["catalog_id"])


# ───────────── 결정론
@pytest.mark.parametrize("diag", DIAGS)
def test_same_input_same_result_even_if_catalog_order_changes(diag, monkeypatch):
    first = _exps(_si(), _ar(diag))
    assert first == _exps(_si(), _ar(diag))
    monkeypatch.setattr(C, "_catalog", lambda: list(reversed(CAT)))
    assert [e["catalog_id"] for e in _exps(_si(), _ar(diag))] == [e["catalog_id"] for e in first]


# ───────────── 순위 규칙(decisions_C ②)
@pytest.mark.parametrize("diag", DIAGS)
def test_ranking_order_follows_rule(diag):
    by_id = {x["id"]: x for x in CAT}
    ranked = [by_id[i] for i in _design(_si(budget_krw=1_000_000, staff_am=3, staff_pm=3,
                                             changeable=["menu", "hours", "price", "channel"]),
                                         _ar(diag))["trace"][-1]["ranked"]]
    keys = [C.rank_key(x, diag) for x in ranked]
    assert keys == sorted(keys)
    primary = [x["fits"][0] == diag for x in ranked]
    assert primary == sorted(primary, reverse=True)  # 주 대상 실험이 모두 앞에


def test_measure_ease_beats_cost():
    # only_me: X04(비용 0, 측정 1) 보다 X10(3만 원, 측정 3)이 앞
    ids = [e["catalog_id"] for e in _exps(_si(), _ar("only_me"))]
    assert ids.index("X10") < ids.index("X04") if "X04" in ids else "X10" in ids


def test_excluded_reasons_are_recorded():
    tr = _design(_si(), _ar("co_decline"))["trace"][-1]
    assert "X09" in tr["excluded"] and any("오전 2명" in r for r in tr["excluded"]["X09"])
    assert any("예산" in r for r in tr["excluded"]["X06"]) and any("영업시간" in r for r in tr["excluded"]["X05"])


def test_rationale_mentions_estimate_and_measurement():
    exps = _exps(_si(), _ar("season_weather"))
    for e in exps:
        t = e["rationale"]["text"]
        assert "2주 뒤" in t and ("추정치" in t or "추가 비용 없이" in t)


# ───────────── region_only / 판단 불가 / 후보 없음
@pytest.mark.parametrize("ar", [_ar("co_decline", "region_only"), _ar("undetermined")])
def test_no_recommendation_without_store_diagnosis(ar):
    rep = _design(_si(), ar)["report"]
    assert rep["_experiments"] == [] and rep["_design_note"] == "region_only"


def _full(si, ar):
    st = {"store_input": si, "analysis_result": ar, "trace": []}
    st |= C.design_experiment(st)
    st |= C.compose_report(st)
    return st


def test_region_only_report_has_notice():
    st = _full(_si(), _ar("undetermined", "region_only"))
    r = st["report"]
    assert r["experiment"] is None and r["alternatives"] == []
    assert "동네 흐름만" in r["limits"][0]["text"]
    assert C.verify(st)["verify_result"]["passed"]


def test_no_feasible_report_asks_for_more_conditions():
    st = _full(_si(budget_krw=0, changeable=[]), _ar("season_weather"))
    r = st["report"]
    assert r["experiment"] is None and st["report"]["_design_note"] == "no_feasible"
    assert any("찾지 못했습니다" in s["text"] for s in r["to_check"])


# ───────────── 검증·제거 연동
def test_verify_catches_staff_and_changeable_violation_then_drop_removes_it():
    st = _full(_si(staff_am=2, changeable=["menu", "channel", "price", "hours"]), _ar("co_decline"))
    assert C.verify(st)["verify_result"]["passed"]
    # 설계가 끝난 뒤 점주 조건이 더 좁았다고 가정(혼자 운영 + 메뉴만 변경 가능) → 기존 추천이 위반이 됨
    st["store_input"] = _si(staff_am=1, staff_pm=1, changeable=["menu"])
    vr = C.verify(st)["verify_result"]
    reasons = [v["reason"] for v in vr["violations"] if v["type"] == "constraint_violation"]
    assert reasons and not vr["passed"]
    st["verify_result"] = vr
    out = C.drop_violations(st)["report"]
    Report.model_validate({k: v for k, v in out.items() if not k.startswith("_")})
    st["report"] = out
    assert not [v for v in C.verify(st)["verify_result"]["violations"] if v["type"] == "constraint_violation"]


def test_verify_flags_unknown_catalog_id():
    st = _full(_si(), _ar("co_decline"))
    st["report"]["experiment"]["catalog_id"] = "X99"
    reasons = [v["reason"] for v in C.verify(st)["verify_result"]["violations"]]
    assert any("X99" in r for r in reasons)


def test_synthetic_stores_end_to_end_pass_verify():
    from coach.graph import run
    for c in json.loads((MOCK_DIR / "synthetic_stores.json").read_text(encoding="utf-8")):
        out = run("손님이 줄었어요", c["store_input"])
        assert out["status"] == "done" and out["verify_result"]["passed"], c["case_id"]
