"""C1 행동 카탈로그 검사: 형식·구성 기준(decisions_C ①)·금지 표현·조건 필터."""
import json
import re

import pytest

from coach.config import BANNED_PHRASES, MOCK_DIR
from coach.contracts import Changeable, Diagnosis, Experiment, Sentence, StoreInput
from coach.nodes import c_experiment as C

CAT = C._catalog()
FIELDS = {"id", "title", "fits", "requires", "cost_krw", "extra_staff",
          "min_staff_am", "min_staff_pm", "measure_ease", "metric", "stop_rule"}
DIAGS = [d.value for d in Diagnosis if d is not Diagnosis.undetermined]
# CLAUDE.md §5 금지 해석에서 카탈로그 문구에 나오면 안 되는 말
EXTRA_BANNED = ["관광객", "반경", "방문객", "구매전환", "원인"]


def test_catalog_has_15_unique_items_with_exact_fields():
    assert len(CAT) == 15
    ids = [x["id"] for x in CAT]
    assert len(set(ids)) == 15 and all(re.fullmatch(r"X\d{2}", i) for i in ids)
    for x in CAT:
        assert set(x) == FIELDS, x["id"]


@pytest.mark.parametrize("x", CAT, ids=lambda x: x["id"])
def test_item_values_are_valid(x):
    assert set(x["fits"]) and set(x["fits"]) <= set(DIAGS)
    assert set(x["requires"]) <= {c.value for c in Changeable}
    assert 0 <= x["cost_krw"] <= 500_000
    assert x["extra_staff"] >= 0 and x["min_staff_am"] >= 1 and x["min_staff_pm"] >= 1
    assert x["measure_ease"] in (1, 2, 3)
    assert "해당 없음" not in x["stop_rule"]  # 중단 기준은 점주가 실제로 판단할 수 있어야 함
    # 리포트에 실릴 Experiment 형식으로 바로 바뀌는지
    Experiment(catalog_id=x["id"], title=x["title"], cost_krw=x["cost_krw"], extra_staff=x["extra_staff"],
               metric=x["metric"], stop_rule=x["stop_rule"], rationale=Sentence(text="-"))


@pytest.mark.parametrize("x", CAT, ids=lambda x: x["id"])
def test_no_banned_wording(x):
    text = " ".join([x["title"], x["metric"], x["stop_rule"]])
    for b in BANNED_PHRASES + EXTRA_BANNED:
        assert b not in text, f"{x['id']}: '{b}'"


def test_composition_follows_decision():
    for d in DIAGS:
        assert sum(d in x["fits"] for x in CAT) >= 5, d           # 유형별 후보 ≥ 5
    assert sum(x["cost_krw"] == 0 for x in CAT) >= len(CAT) / 3  # 비용 0원 ≥ 1/3
    over = [x for x in CAT if x["cost_krw"] > 300_000]
    assert 2 <= len(over) <= 3                                   # 예산 필터 시험용 초과 항목


# ───────────── 조건 필터
def _stores():
    return {c["case_id"]: c["store_input"] for c in
            json.loads((MOCK_DIR / "synthetic_stores.json").read_text(encoding="utf-8"))}


@pytest.mark.parametrize("diag", DIAGS)
def test_synthetic_store_keeps_at_least_3_candidates_per_diagnosis(diag):
    si = StoreInput.model_validate(_stores()["S-co_decline"])  # 예산 30만·오전1/오후2·[menu,channel,price]
    assert sum(diag in x["fits"] and C.feasible(x, si) for x in CAT) >= 3


def _si(**kw):
    base = dict(budget_krw=300_000, staff_am=1, staff_pm=2, changeable=["menu", "channel", "price"])
    return StoreInput.model_validate(base | kw)


def test_filter_blocks_each_condition():
    by_id = {x["id"]: x for x in CAT}
    assert not C.feasible(by_id["X15"], _si())                   # 35만 원 > 예산 30만
    assert C.feasible(by_id["X15"], _si(budget_krw=400_000))
    assert not C.feasible(by_id["X09"], _si())                   # 오전 2명 필요, 점주 1명
    assert C.feasible(by_id["X09"], _si(staff_am=2))
    assert not C.feasible(by_id["X05"], _si())                   # 영업시간 변경 불가
    assert C.feasible(by_id["X05"], _si(changeable=["hours"]))
    assert not C.feasible(by_id["X06"], _si(budget_krw=1_000_000, staff_am=3))  # 추가 인력 필요


def test_zero_budget_solo_store_gets_only_free_solo_items():
    si = _si(budget_krw=0, staff_am=1, staff_pm=1, changeable=["menu", "hours", "price", "channel"])
    ok = [x for x in CAT if C.feasible(x, si)]
    assert ok and all(x["cost_krw"] == 0 and x["min_staff_am"] == 1 and x["min_staff_pm"] == 1 for x in ok)
