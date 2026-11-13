"""B 슬롯 추출 테스트 — 실제 LLM 호출 없이(가짜 응답) 돌아간다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pytest

from coach.llm import client as llm
from coach.nodes import b_orchestrator as B


# ── 규칙 기반 경로(LLM 장애 시 대체 경로)
@pytest.mark.parametrize("q, expected", [
    ("애월에서 카페해요. 오전 2명, 오후 1명이에요", {"staff_am": 2, "staff_pm": 1}),
    ("오전엔 둘, 오후에는 한 명", {"staff_am": 2, "staff_pm": 1}),
    ("혼자 운영해요", {"staff_am": 1, "staff_pm": 1}),
    ("예산은 없어요", {"budget_krw": 0}),
    ("예산 30만 원 정도", {"budget_krw": 300000}),
])
def test_rule_extract_staff_and_budget(q, expected):
    out = B._rule_extract(q)
    for k, v in expected.items():
        assert out.get(k) == v, (q, out)


def test_rule_full_sentence_has_no_missing():
    st = B.parse_input({"question": "애월읍 카페, 예산 10만 원, 오전 1명 오후 2명", "trace": [],
                        "llm_available": False})
    assert st["missing_slots"] == []


# ── LLM 경로(가짜 응답)
def _fake(slots: B.ExtractedSlots):
    """정해진 답(slots)만 돌려주는 가짜 LLM 함수를 만든다."""
    def f(prompt, schema):
        assert "애월읍" in prompt and schema is B.ExtractedSlots  # 지역 목록·형식이 제대로 전달됐는지
        return slots
    return f


def test_llm_path_maps_dong_code(monkeypatch):
    monkeypatch.setattr(llm, "extract_structured",
                        _fake(B.ExtractedSlots(dong_name="애월읍", budget_krw=300000, staff_am=2, staff_pm=1,
                                               changeable=["menu", "menu"])))
    st = B.parse_input({"question": "애월 카페", "trace": [], "llm_available": True})
    si = st["store_input"]
    assert si["dong_code"] == "5011025000" and si["changeable"] == ["menu"]
    assert st["missing_slots"] == [] and st["llm_available"] is True


def test_llm_hallucinated_dong_is_dropped(monkeypatch):
    monkeypatch.setattr(llm, "extract_structured", _fake(B.ExtractedSlots(dong_name="서귀포 어딘가")))
    st = B.parse_input({"question": "카페해요", "trace": [], "llm_available": True})
    assert "dong_code" in st["missing_slots"]


def test_llm_failure_falls_back_to_rules(monkeypatch):
    def boom(prompt, schema):
        raise llm.LLMError("한도 초과")
    monkeypatch.setattr(llm, "extract_structured", boom)
    st = B.parse_input({"question": "애월읍 카페, 예산 10만 원, 오전 1명 오후 1명", "trace": [],
                        "llm_available": True})
    assert st["llm_available"] is False and st["missing_slots"] == []
    assert "한도 초과" in st["trace"][-1]["llm_error"]


def test_form_input_overrides_llm(monkeypatch):
    monkeypatch.setattr(llm, "extract_structured", _fake(B.ExtractedSlots(dong_name="애월읍", budget_krw=50000)))
    st = B.parse_input({"question": "애월", "trace": [], "llm_available": True,
                        "store_input": {"budget_krw": 200000, "staff_am": 1, "staff_pm": 1}})
    assert st["store_input"]["budget_krw"] == 200000  # 양식 입력 우선


def test_no_api_key_raises(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(llm.LLMError):
        llm.extract_structured("x", B.ExtractedSlots)