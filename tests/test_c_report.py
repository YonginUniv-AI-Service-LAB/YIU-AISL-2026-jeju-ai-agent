"""C3 리포트·검증 루프 검사: 점주 말투 템플릿, 문장 속 숫자 대조, A notes 처리, LLM 다듬기·재생성(가짜 LLM)."""
import json
import re

import pytest

from coach.config import MOCK_DIR
from coach.graph import run
from coach.llm import client as llm
from coach.nodes import a_data as A, b_orchestrator as B, c_experiment as C

CASES = json.loads((MOCK_DIR / "synthetic_stores.json").read_text(encoding="utf-8"))
SI = CASES[0]["store_input"]  # 애월읍 · 동네 동반 하락형


def _state(notes=()):
    st = {"store_input": SI, "trace": []}
    st |= A.check_fitness(st)
    st |= A.analyze(st)
    st["analysis_result"]["notes"] += list(notes)
    st |= C.design_experiment(st)
    st |= C.compose_report(st)
    return st


def _types(st):
    return [v["type"] for v in C.verify(st)["verify_result"]["violations"]]


# ───────────── 템플릿(LLM 없음)
@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_template_reads_naturally_and_passes(case):
    out = run("손님이 줄었어요", case["store_input"])
    r = out["report"]
    assert out["verify_result"]["passed"] and r["_polish"] is None  # 테스트는 LLM 꺼짐
    for sec in C.POLISH_SECTIONS:
        for s in r[sec]:
            assert not re.search(r"-\d", s["text"]), s["text"]  # 방향은 부호 대신 '늘었/줄었'
    assert any("줄었" in s["text"] or "늘었" in s["text"] for s in r["observed"])


def test_number_in_sentence_must_match_cited_evidence():
    st = _state()
    s = st["report"]["observed"][0]
    s["text"] = re.sub(r"\d+\.\d", "41.0", s["text"], count=1)  # 근거 번호는 그대로, 문장 숫자만 바꿈
    vio = C.verify(st)["verify_result"]["violations"]
    assert any(v["type"] == "number_mismatch" and "문장 속 41.0%" in v["reason"] for v in vio)


# ───────────── A notes (decisions_C ⑥)
def test_notes_converted_and_exempt_from_number_check():
    st = _state(notes=["[범위 축소] 주문 기록 없음 → 12주가 필요해 동네 흐름만 제공",
                       "[판정 불가] 외국인 값 12.5% 빠짐 → 외국인 흐름 표시 안 함"])
    lim = [s["text"] for s in st["report"]["limits"]]
    assert "이번에는 범위를 줄여 답했어요: 주문 기록 없음, 그래서 12주가 필요해 동네 흐름만 제공." in lim
    assert any(t.startswith("판단할 수 없는 부분이 있어요: ") and "12.5%" in t for t in lim)
    assert not any("→" in t for t in lim)
    assert _types(st) == []  # '12.5%'가 있어도 A 사유는 지워지지 않음


def test_notes_still_checked_for_banned_phrase():
    st = _state(notes=["[범위 축소] 반경 500m 자료 없음"])
    assert "banned_phrase" in _types(st)


# ───────────── LLM 다듬기 + 재생성 루프 (가짜 LLM)
@pytest.fixture
def fake_llm(monkeypatch):
    """B 조건 추출은 규칙으로, 실제 LLM 호출은 막고, C 다듬기만 가짜 함수로 바꾼다."""
    monkeypatch.setattr(B, "_llm_extract", B._rule_extract)
    monkeypatch.setattr(llm, "extract_structured", lambda *a, **k: (_ for _ in ()).throw(llm.LLMError("테스트: 호출 금지")))
    calls = []

    def install(fn):
        def wrapped(texts, feedback):
            calls.append(list(feedback))
            return fn(texts, feedback, len(calls))
        monkeypatch.setattr(C, "_llm_polish", wrapped)
        return calls
    return install


def _tamper(texts):
    return [re.sub(r"\d+\.\d", "99.9", texts[0], count=1)] + [f"(다듬음) {t}" for t in texts[1:]]


def _go():
    out = run("손님이 줄었어요", SI, llm_available=True)
    comp = [t for t in out["trace"] if t["node"] == "C.compose_report"]
    ver = [t for t in out["trace"] if t["node"] == "C.verify"]
    return out, comp, ver


TEMPLATE = run("손님이 줄었어요", SI)["report"]


def test_llm_polish_keeps_claim_ids(fake_llm):
    fake_llm(lambda texts, fb, n: [f"(다듬음) {t}" for t in texts])
    out, comp, _ = _go()
    r = out["report"]
    assert out["verify_result"]["passed"] and len(comp) == 1 and comp[0]["polish"]["action"] == "polished"
    for sec in C.POLISH_SECTIONS:
        assert [s["claim_ids"] for s in r[sec]] == [s["claim_ids"] for s in TEMPLATE[sec]]
        assert all(s["text"].startswith("(다듬음) ") for s in r[sec])
    assert r["limits"] == TEMPLATE["limits"]  # 한계는 LLM 이 건드리지 않음


def test_regeneration_with_feedback_improves(fake_llm):
    calls = fake_llm(lambda texts, fb, n: _tamper(texts) if n == 1 else [f"(고침) {t}" for t in texts])
    out, comp, ver = _go()
    assert [v["passed"] for v in ver] == [False, True]                       # 재생성 뒤 개선
    assert comp[1]["polish"] == {"action": "repolished", "fixed": [0]}
    assert calls[0] == [] and "99.9" in calls[1][0] and "근거 값" in calls[1][0]  # 위반 사유를 LLM 에 전달
    r = out["report"]
    assert r["observed"][0]["text"].startswith("(고침) ") and r["observed"][1]["text"].startswith("(다듬음) ")


def test_persistent_violation_falls_back_to_template(fake_llm):
    fake_llm(lambda texts, fb, n: _tamper(texts))
    out, comp, ver = _go()
    assert [v["passed"] for v in ver] == [False, False, True] and out["status"] == "done"
    assert comp[-1]["polish"]["action"] == "reverted"
    assert out["report"]["observed"][0]["text"] == TEMPLATE["observed"][0]["text"]


def test_llm_failure_uses_template(fake_llm):
    def boom(texts, fb, n):
        raise llm.LLMError("timeout")
    fake_llm(boom)
    out, comp, _ = _go()
    assert out["verify_result"]["passed"] and comp[0]["polish"]["action"] == "template"
    assert all(out["report"][s] == TEMPLATE[s] for s in C.POLISH_SECTIONS)


def test_no_llm_call_when_unavailable(monkeypatch):
    monkeypatch.setattr(C, "_llm_polish", lambda *a: pytest.fail("LLM 꺼짐인데 호출됨"))
    assert run("손님이 줄었어요", SI)["verify_result"]["passed"]


def test_polish_prompt_fills_placeholders(monkeypatch):
    seen = {}

    def fake(prompt, schema):
        seen["p"] = prompt
        return schema(sentences=["a", "b"])
    monkeypatch.setattr(llm, "extract_structured", fake)
    assert C._llm_polish(["x 1.0%", "y"], ["1번 문장: 문제"]) == ["a", "b"]
    assert "{" + "sentences}" not in seen["p"] and "x 1.0%" in seen["p"] and "1번 문장: 문제" in seen["p"]
    assert "'원인은'" in seen["p"]
    monkeypatch.setattr(llm, "extract_structured", lambda p, s: s(sentences=["only one"]))
    with pytest.raises(llm.LLMError):
        C._llm_polish(["x", "y"], [])
