"""B 오케스트레이터·대화 — 슬롯 추출 / 되묻기 / 라우팅 / 재방문 점검 / LLM 장애 대체.

슬롯 추출: COACH_LLM=1 이면 LLM 구조화 출력(ExtractedSlots) → 실패 시 규칙 기반 경로로 대체.
COACH_LLM=0(기본, mock) 이면 규칙 기반만 사용.
"""
from __future__ import annotations

import json
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from ..config import MAX_RETRIES, MOCK_DIR, PROMPT_DIR, USE_LLM
from ..contracts import Changeable, StoreInput
from ..llm import client as llm
from ..trace import log

# ───────────────────────── 슬롯(조건) 정의

# 이 4개가 다 채워져야 분석으로 넘어감. 하나라도 비면 되묻기.
REQUIRED = ["dong_code", "budget_krw", "staff_am", "staff_pm"]
ASK = {
    "dong_code": "가게가 있는 읍·면·동을 알려주세요. (예: 애월읍)",
    "budget_krw": "이번 달 실험에 쓸 수 있는 예산은 얼마인가요? (0원도 괜찮아요)",
    "staff_am": "오전에 일하는 인원은 몇 명인가요?",
    "staff_pm": "오후에 일하는 인원은 몇 명인가요?",
}
# "바꿀 수 있는 항목"을 찾는 키워드
_KW = {Changeable.menu: "메뉴", Changeable.hours: "시간", Changeable.price: "가격", Changeable.channel: "배달|SNS|테이크아웃"}


# ───────────────────────── 규칙 기반 추출 (LLM 장애 시 대체 경로)

# 한글 수사 → 숫자 ("둘" → 2)
_KNUM = {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4, "다섯": 5}
_NUM = r"(\d+|다섯|하나|한|둘|두|셋|세|넷|네)"


def _to_int(s: str) -> int:
    return int(s) if s.isdigit() else _KNUM[s]


def _dong_codes() -> dict[str, str]:
    """{읍면동 이름: 행정코드}. A의 매핑표에서 읽음('_'로 시작하는 메모 키 제외)."""
    d = json.loads((MOCK_DIR / "dong_codes.json").read_text(encoding="utf-8"))
    return {k: v for k, v in d.items() if not k.startswith("_")}


def _rule_extract(q: str) -> dict:
    """정규식으로 조건 추출. LLM 없이도 대화가 끝까지 가게 하는 안전망."""
    out: dict = {}
    for name, code in _dong_codes().items():
        alias = name[:-1] if len(name) > 2 else name  # "애월읍" → "애월"
        if name in q or alias in q:
            out.update(dong_name=name, dong_code=code)
    # 예산: "예산 30만" → 300000, "예산 없어요" → 0
    if m := re.search(r"예산[^0-9]*(\d+)\s*만", q):
        out["budget_krw"] = int(m.group(1)) * 10000
    elif re.search(r"예산\s*(?:은|이)?\s*(?:없|0\s*원)", q):
        out["budget_krw"] = 0
    # 인력: "오전 2명", "오전엔 둘", "오후에 한 명", "혼자"
    for slot, kw in (("staff_am", "오전"), ("staff_pm", "오후")):
        if m := re.search(kw + r"\s*(?:에는|에|엔|은|는)?\s*" + _NUM + r"\s*(?:명|사람)?", q):
            out[slot] = _to_int(m.group(1))
    if re.search(r"혼자", q):  # 이미 숫자로 말한 값이 있으면 덮어쓰지 않음(setdefault)
        out.setdefault("staff_am", 1)
        out.setdefault("staff_pm", 1)
    ch = [c.value for c, kw in _KW.items() if re.search(kw, q)]
    if ch:
        out["changeable"] = ch
    return out


# ───────────────────────── LLM 추출

class ExtractedSlots(BaseModel):
    """LLM 이 채우는 형식(StoreInput 의 일부). 행정코드는 LLM 이 만들지 않고 코드가 매핑한다."""
    dong_name: Optional[str] = Field(None, description="허용 지역 목록의 이름 그대로, 모르면 null")
    budget_krw: Optional[int] = Field(None, description="원 단위 정수")
    staff_am: Optional[int] = None
    staff_pm: Optional[int] = None
    open_hours: Optional[str] = Field(None, description="HH:MM-HH:MM")
    changeable: list[Literal["menu", "hours", "price", "channel"]] = Field(default_factory=list)


def _llm_extract(q: str) -> dict:
    """LLM 구조화 출력 → StoreInput 부분 dict. 실패하면 llm.LLMError 를 올린다."""
    codes = _dong_codes()
    # 프롬프트의 {dong_list}, {question} 빈칸을 실제 값으로 채움
    prompt = (PROMPT_DIR / "extract_slots.md").read_text(encoding="utf-8").format(
        dong_list=", ".join(codes), question=q)
    s = llm.extract_structured(prompt, ExtractedSlots)
    out: dict = {}
    if s.dong_name in codes:  # 목록 밖 지명(환각)은 버린다
        out.update(dong_name=s.dong_name, dong_code=codes[s.dong_name])
    for k in ("budget_krw", "staff_am", "staff_pm"):  # 음수 같은 이상한 값은 버림
        v = getattr(s, k)
        if v is not None and v >= 0:
            out[k] = v
    if s.open_hours and re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", s.open_hours):
        out["open_hours"] = s.open_hours
    if s.changeable:
        out["changeable"] = list(dict.fromkeys(s.changeable))  # 중복 제거(순서 유지)
    return out


# ───────────────────────── 그래프 노드

def parse_input(state):
    """질문 → 조건 추출(LLM 우선, 실패 시 규칙) → 양식 입력과 합치기 → 빠진 항목 확인."""
    q = state.get("question", "")
    form = state.get("store_input") or {}
    llm_ok = state.get("llm_available", USE_LLM)
    err = None
    try:
        extracted = _llm_extract(q) if llm_ok else _rule_extract(q)
    except Exception as e:  # LLM 장애 → 규칙 경로
        extracted, llm_ok, err = _rule_extract(q), False, f"{type(e).__name__}: {e}"[:200]
    merged = {**extracted, **{k: v for k, v in form.items() if v not in (None, [], "")}}  # 양식 입력 우선
    merged = StoreInput.model_validate(merged).model_dump(mode="json")
    missing = [k for k in REQUIRED if merged.get(k) is None]
    return {"store_input": merged, "missing_slots": missing, "llm_available": llm_ok, "retries": 0,
            "trace": log(state, "B.parse_input", extracted=extracted, missing=missing, llm=llm_ok,
                         llm_error=err, latency_s=llm.LAST_LATENCY_S if llm_ok else None)}


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
    status = "done" if state.get("llm_available") or not USE_LLM else "fallback"
    return {"status": status, "trace": log(state, "B.finish", status=status, retries=state.get("retries", 0))}