"""C4 지원제도 연결 검사: 문서 형식·출처, 태그 매칭 규칙, 리포트·검증 연동."""
import datetime as dt
import json

import pytest

from coach.config import BANNED_PHRASES, MOCK_DIR
from coach.contracts import Changeable, StoreInput
from coach.graph import run
from coach.nodes import c_experiment as C

DOCS = C._support_docs()
FIELDS = {"id", "title", "org", "summary", "fits", "changeable", "experiment_ids", "status", "period", "url", "checked"}
CASES = json.loads((MOCK_DIR / "synthetic_stores.json").read_text(encoding="utf-8"))
CAT_IDS = {x["id"] for x in C._catalog()}


@pytest.mark.parametrize("d", DOCS, ids=lambda d: d["id"])
def test_doc_schema_and_source(d):
    assert set(d) == FIELDS
    assert d["url"].startswith("https://")                       # 출처 URL 필수
    assert d["status"] in C.STATUS_KO
    dt.date.fromisoformat(d["checked"])                          # 확인일 기록
    assert set(d["fits"]) <= {"co_decline", "only_me", "season_weather", "undetermined"} and d["fits"]
    assert set(d["changeable"]) <= {c.value for c in Changeable}
    assert set(d["experiment_ids"]) <= CAT_IDS
    for b in BANNED_PHRASES + ["관광객", "반경", "방문객"]:
        assert b not in d["title"] + d["summary"]


def test_ids_unique_and_each_diagnosis_has_two_or_more():
    assert len({d["id"] for d in DOCS}) == len(DOCS)
    for diag in ("co_decline", "only_me", "season_weather"):
        assert sum(diag in d["fits"] for d in DOCS) >= 2


def _si(**kw):
    return StoreInput.model_validate(dict(budget_krw=300_000, staff_am=1, staff_pm=2,
                                          changeable=["menu", "channel", "price"]) | kw)


def test_scoring_rule():
    got = dict((d["id"], s) for d, s in C.match_support("season_weather", _si(), ["X02"], k=10))
    assert got["P07"] == 2 + 1 + 1          # 실험 X02 관련 + channel 겹침 + 상시
    assert got["P05"] == 1                  # 접수 중만
    assert "P03" not in got                 # 계절형에 맞지 않음
    top = C.match_support("season_weather", _si(), ["X02"])
    assert top[0][0]["id"] == "P07" and len(top) == 3


def test_closed_program_ranks_below_open_with_same_tags():
    ranked = [d["id"] for d, _ in C.match_support("only_me", _si(changeable=[]), [], k=10)]
    assert ranked.index("P04") > ranked.index("P05")  # P04 마감, P05 접수 중


def test_deterministic_and_order_independent(monkeypatch):
    a = C.match_support("co_decline", _si(), ["X08", "X07", "X01"])
    monkeypatch.setattr(C, "_support_docs", lambda: list(reversed(DOCS)))
    assert [d["id"] for d, _ in C.match_support("co_decline", _si(), ["X08", "X07", "X01"])] == [d["id"] for d, _ in a]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_report_support_has_url_and_passes_verify(case):
    out = run("손님이 줄었어요", case["store_input"])
    sp = out["report"]["support_programs"]
    assert 1 <= len(sp) <= 3 and all("출처 https://" in s["text"] and "확인)" in s["text"] for s in sp)
    assert any("확인일 기준" in s["text"] for s in out["report"]["limits"])
    assert out["verify_result"]["passed"]


def test_verify_flags_support_without_url():
    out = run("손님이 줄었어요", CASES[0]["store_input"])
    st = {"store_input": out["store_input"], "analysis_result": out["analysis_result"], "report": out["report"], "trace": []}
    st["report"]["support_programs"].append({"text": "어떤 지원금이 있어요", "claim_ids": []})
    vio = C.verify(st)["verify_result"]["violations"]
    assert any(v["section"] == "support_programs" and v["reason"] == "출처 URL 없음" for v in vio)
