"""★B 오케스트레이터·대화 — 슬롯 추출 / 되묻기 / 라우팅 / 재방문 점검 / LLM 장애 대체.

mock 모드: 규칙 기반 슬롯 추출(LLM 없음).
실제 모드 TODO: LLM 구조화 출력(StoreInput 스키마)으로 추출 → 실패 시 이 규칙 경로로 대체.
"""
from __future__ import annotations

import json
import re

from ..config import MAX_RETRIES, MOCK_DIR, USE_MOCK
from ..contracts import Changeable, StoreInput
from ..trace import log

REQUIRED = ["dong_code", "budget_krw", "staff_am", "staff_pm"]
ASK = {
    "dong_code": "가게가 있는 읍·면·동을 알려주세요. (예: 애월읍)",
    "budget_krw": "이번 달 실험에 쓸 수 있는 예산은 얼마인가요? (0원도 괜찮아요)",
    "staff_am": "오전에 일하는 인원은 몇 명인가요?",
    "staff_pm": "오후에 일하는 인원은 몇 명인가요?",
}
_KW = {Changeable.menu: "메뉴", Changeable.hours: "시간", Changeable.price: "가격", Changeable.channel: "배달|SNS|테이크아웃"}


def _rule_extract(q: str) -> dict:
    dongs = json.loads((MOCK_DIR / "dong_codes.json").read_text(encoding="utf-8"))
    out: dict = {}
    for name, code in dongs.items():
        alias = name[:-1] if len(name) > 2 else name  # "애월읍" → "애월"
        if not name.startswith("_") and (name in q or alias in q):
            out.update(dong_name=name, dong_code=code)
    if m := re.search(r"예산[^0-9]*(\d+)\s*만", q):
        out["budget_krw"] = int(m.group(1)) * 10000
    ch = [c.value for c, kw in _KW.items() if re.search(kw, q)]
    if ch:
        out["changeable"] = ch
    return out


def _llm_extract(q: str) -> dict:
    raise NotImplementedError("TODO(B): LLM structured output → StoreInput 부분 dict")


def parse_input(state):
    q = state.get("question", "")
    form = state.get("store_input") or {}
    llm_ok = state.get("llm_available", not USE_MOCK)
    try:
        extracted = _llm_extract(q) if llm_ok else _rule_extract(q)
    except Exception as e:  # LLM 장애 → 규칙 경로
        extracted, llm_ok = _rule_extract(q), False
    merged = {**extracted, **{k: v for k, v in form.items() if v not in (None, [], "")}}  # 양식 입력 우선
    merged = StoreInput.model_validate(merged).model_dump(mode="json")
    missing = [k for k in REQUIRED if merged.get(k) is None]
    return {"store_input": merged, "missing_slots": missing, "llm_available": llm_ok, "retries": 0,
            "trace": log(state, "B.parse_input", extracted=extracted, missing=missing, llm=llm_ok)}


def ask_back(state):
    msg = " / ".join(ASK[k] for k in state["missing_slots"])
    return {"ask_back": msg, "status": "need_input", "trace": log(state, "B.ask_back", ask=msg)}


def review_experiment(state):
    """2주 후 재방문: 직전 실험 기간(마지막 2주) vs 그 전 2주. 효과 불분명하면 '확인 불가'."""
    prev = state.get("previous_experiment")
    weeks = sorted(state["store_input"].get("weeks", []), key=lambda w: w["week_start"])
    if not prev or len(weeks) < 4:
        rv = {"catalog_id": prev and prev.get("catalog_id"), "verdict": "확인 불가", "reason": "비교할 실적 부족"}
    else:
        a = sum(w["orders"] for w in weeks[-2:]); b = sum(w["orders"] for w in weeks[-4:-2])
        ch = round((a - b) / b * 100, 1) if b else 0.0
        verdict = "개선 신호" if ch >= 5 else ("악화 신호" if ch <= -5 else "확인 불가")
        rv = {"catalog_id": prev.get("catalog_id"), "orders_change_pct": ch, "verdict": verdict,
              "reason": "단일 점포 전후 비교 — 효과를 확정하지 않음(날씨·휴일 차이 가능)"}
    return {"experiment_review": rv, "trace": log(state, "B.review_experiment", **rv)}


# ── 라우터
def route_after_parse(state):
    if state["missing_slots"]:
        return "ask_back"
    return "review_experiment" if state.get("previous_experiment") else "check_fitness"


def route_after_verify(state):
    if state["verify_result"]["passed"]:
        return "finish"
    return "compose_report" if state.get("retries", 0) < MAX_RETRIES else "drop_violations"


def bump_retry(state):
    return {"retries": state.get("retries", 0) + 1}


def finish(state):
    status = "done" if state.get("llm_available") or USE_MOCK else "fallback"
    return {"status": status, "trace": log(state, "B.finish", status=status, retries=state.get("retries", 0))}
