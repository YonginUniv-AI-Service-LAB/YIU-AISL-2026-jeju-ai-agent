"""★C 실험 설계·검증 — design_experiment / compose_report / verify / drop_violations.

설계: 카탈로그 → 조건 필터 → 규칙 순위 → 추천 1 + 대안 2 (decisions_C ①②)
리포트: 점주 말투 템플릿 → LLM 사용 가능 시 문장만 다듬기(숫자·claim_id 는 코드 고정, 실패 시 템플릿) (③)
검증: 금지 표현·근거 누락·A recompute 재계산·문장 속 숫자 대조·과거 자료·점주 조건. A notes 는 금지 표현만(⑥)
"""
from __future__ import annotations

import json
import re

import yaml
from pydantic import BaseModel

from ..config import BANNED_PHRASES, MAX_RETRIES, MOCK_DIR, PROMPT_DIR, USE_LLM
from ..contracts import (AnalysisResult, Diagnosis, Experiment, Mode, Report, Sentence, StoreInput, VerifyResult,
                         Violation)
from ..llm import client as llm
from ..trace import log
from .a_data import recompute

DIAG_KO = {"co_decline": "동네 동반 하락형", "only_me": "나만 하락형",
           "season_weather": "계절·날씨형", "undetermined": "판단 불가"}


def _catalog():
    return yaml.safe_load((MOCK_DIR / "catalog.yaml").read_text(encoding="utf-8"))


CHANGEABLE_KO = {"menu": "메뉴", "hours": "영업시간", "price": "가격", "channel": "판매 채널"}
EASE_KO = {3: "POS 주문·매출 기록만으로", 2: "POS 기록과 간단한 메모(폐기·할인액 등)로",
           1: "지도앱·배달앱 통계나 손님 표본 기록으로"}


def infeasible_reasons(item: dict, si: StoreInput) -> list[str]:
    """점주 조건(예산·인력·바꿀 수 있는 항목)을 넘는 이유 목록. 비어 있으면 실행 가능. 진단 유형과는 무관."""
    staff_am = 1 if si.staff_am is None else si.staff_am  # B 가 되묻기로 채우므로 그래프에서는 항상 값이 있음
    staff_pm = 1 if si.staff_pm is None else si.staff_pm
    budget = si.budget_krw or 0
    why = []
    if item["cost_krw"] > budget:
        why.append(f"예상 비용 {item['cost_krw']:,}원이 예산 {budget:,}원을 넘음")
    if item["extra_staff"] > 0:  # 채용 가능 여부 입력이 없어 추가 인력은 불가로 가정
        why.append(f"추가 인력 {item['extra_staff']}명 필요")
    if item["min_staff_am"] > staff_am:
        why.append(f"오전 {item['min_staff_am']}명 필요(현재 {staff_am}명)")
    if item["min_staff_pm"] > staff_pm:
        why.append(f"오후 {item['min_staff_pm']}명 필요(현재 {staff_pm}명)")
    missing = [r for r in item["requires"] if r not in {c.value for c in si.changeable}]
    if missing:
        why.append("바꿀 수 없는 항목 필요: " + ", ".join(CHANGEABLE_KO[r] for r in missing))
    return why


def feasible(item: dict, si: StoreInput) -> bool:
    return not infeasible_reasons(item, si)


def rank_key(item: dict, diag: str):
    """순위 규칙(decisions_C ②, A안). 작은 값이 앞. 마지막 id 는 동점일 때 순서를 고정(결정론)."""
    return (0 if item["fits"][0] == diag else 1,  # ① 이 진단이 주 대상인 실험 우선
            -item["measure_ease"],                 # ② 2주 뒤 판단하기 쉬운 실험 우선
            item["cost_krw"],                      # ③ 싼 실험 우선
            item["id"])


def _rationale(item: dict, diag: str) -> Sentence:
    fit = "주로 맞는" if item["fits"][0] == diag else "함께 고려할 수 있는"
    cost = "추가 비용 없이" if item["cost_krw"] == 0 else f"예상 비용 약 {item['cost_krw']:,}원(추정치)으로"
    return Sentence(text=f"{DIAG_KO[diag]}에 {fit} 실험입니다. {cost} 점주님 인력·바꿀 수 있는 항목 안에서 할 수 있고, "
                         f"2주 뒤 {EASE_KO[item['measure_ease']]} 결과를 판단할 수 있습니다.")


def design_experiment(state):
    """카탈로그 → 진단 유형 → 조건 필터 → 순위 → 추천 1 + 대안 2. 같은 입력이면 같은 결과."""
    si = StoreInput.model_validate(state["store_input"])
    ar = AnalysisResult.model_validate(state["analysis_result"])
    diag = ar.diagnosis.value
    rep = dict(state.get("report") or {})
    if ar.mode == Mode.region_only or ar.diagnosis == Diagnosis.undetermined:
        rep["_experiments"], rep["_design_note"] = [], "region_only"  # 내 가게 진단이 없으면 실험도 추천하지 않음
        return {"report": rep, "trace": log(state, "C.design_experiment", candidates=[], note="region_only",
                                            mode=ar.mode.value, diagnosis=diag)}
    fitting = [x for x in _catalog() if diag in x["fits"]]
    excluded = {x["id"]: r for x in fitting if (r := infeasible_reasons(x, si))}
    cands = sorted((x for x in fitting if x["id"] not in excluded), key=lambda x: rank_key(x, diag))
    exps = [Experiment(catalog_id=x["id"], title=x["title"], cost_krw=x["cost_krw"], extra_staff=x["extra_staff"],
                       metric=x["metric"], stop_rule=x["stop_rule"], rationale=_rationale(x, diag))
            for x in cands[:3]]
    rep["_experiments"] = [e.model_dump(mode="json") for e in exps]
    rep["_design_note"] = None if exps else "no_feasible"
    return {"report": rep, "trace": log(state, "C.design_experiment", candidates=[e.catalog_id for e in exps],
                                        ranked=[x["id"] for x in cands], excluded=excluded)}


POLISH_SECTIONS = ["observed", "my_store", "hypotheses", "to_check"]  # LLM 이 다듬을 수 있는 곳(한계·지원제도는 고정)
DIAG_HYP = {  # 원인 후보(단정 금지). A classify 규칙의 의미를 점주 말로 옮김
    "co_decline": "동네 전체 방문 흐름이 줄어든 시기에 우리 가게 주문도 비슷한 폭으로 줄었어요. "
                  "함께 나타난 변화일 뿐, 이것만으로 이유를 확정할 수는 없어요.",
    "only_me": "동네 방문 흐름은 크게 줄지 않았는데 우리 가게 주문은 줄었어요. "
               "메뉴·가격·매장 정보처럼 가게 쪽에서 바꿔 볼 수 있는 부분이 먼저 살펴볼 후보예요.",
    "season_weather": "동네 흐름이 직전 6주보다는 줄었지만 작년 같은 시기와는 크게 다르지 않아, "
                      "해마다 이맘때 나타나는 계절 흐름일 가능성이 있어요.",
}


def _chg(v: float) -> str:
    """증감률 → 점주 말('14.0% 줄었'). 숫자는 근거 값의 절댓값 그대로(verify 가 문장 속 숫자를 근거와 대조)."""
    if abs(v) < 0.05:
        return "거의 변하지 않았"
    return f"{abs(v):.1f}% {'늘었' if v > 0 else '줄었'}"


def note_sentence(note: str) -> str:
    """A 판정 사유(notes) → 점주 말. 코드가 만든 사유라 verify 는 금지 표현만 검사(decisions_C ⑥)."""
    head, body = "참고로 알려 드려요", note
    for tag, h in (("[범위 축소]", "이번에는 범위를 줄여 답했어요"), ("[판정 불가]", "판단할 수 없는 부분이 있어요")):
        if note.startswith(tag):
            head, body = h, note[len(tag):].strip()
    body = body.replace(" → ", ", 그래서 ").replace("→", ", 그래서 ").rstrip(". ")
    return f"{head}: {body}."


def _template_sections(si: StoreInput, ar: AnalysisResult, note: str | None) -> dict[str, list[Sentence]]:
    m, ev = ar.metrics, {e.metric: e.claim_id for e in ar.evidence}
    dong = si.dong_name or "해당 지역"
    diag = ar.diagnosis.value
    observed, my_store, hyp = [], [], []
    if m.get("region_visit_prev_pct") is not None and m.get("region_visit_yoy_pct") is not None:
        observed.append(Sentence(
            text=f"{dong} 전체의 내국인 방문 유동인구는 최근 6주 평균이 직전 6주보다 {_chg(m['region_visit_prev_pct'])}고, "
                 f"작년 같은 시기보다 {_chg(m['region_visit_yoy_pct'])}어요.",
            claim_ids=[ev["region_visit_prev_pct"], ev["region_visit_yoy_pct"]]))
        observed.append(Sentence(text="읍면동 전체를 집계한 숫자라서 우리 가게에 온 손님 수와는 달라요."))
    fp, fy = m.get("region_visit_for_prev_pct"), m.get("region_visit_for_yoy_pct")
    if fp is not None and fy is not None:
        observed.append(Sentence(
            text=f"같은 기간 외국인 단기체류 유동인구는 직전 6주보다 {_chg(fp)}고, 작년 같은 시기보다 {_chg(fy)}어요"
                 f"(내국인과 따로 집계한 보조 지표예요).",
            claim_ids=[ev["region_visit_for_prev_pct"], ev["region_visit_for_yoy_pct"]]))
    if m.get("my_orders_change_pct") is not None and "my_orders_change_pct" in ev:
        my_store.append(Sentence(text=f"우리 가게 영업일당 주문 수는 최근 6주가 직전 6주보다 {_chg(m['my_orders_change_pct'])}어요.",
                                 claim_ids=[ev["my_orders_change_pct"]]))
        my_store.append(Sentence(
            text=f"동네 흐름과 견주어 보면 '{DIAG_KO[diag]}'에 가깝습니다(근거 강도: {ar.evidence_strength})."
            if diag != "undetermined" else "동네 흐름과 견주어 봐도 어느 유형인지 판단하기 어려워요."))
    if diag in DIAG_HYP and note != "region_only":
        hyp.append(Sentence(text=DIAG_HYP[diag]))
    rain = m.get("region_rain_effect_pp")
    if rain is not None and "region_rain_effect_pp" in ev:
        if rain >= 0.05:
            t = f"비 온 날 수의 변화가 방문을 줄이는 쪽으로 작용한 폭은 약 {rain:.1f}%p로 추정돼요."
        elif rain <= -0.05:
            t = f"비 온 날 수의 변화는 오히려 방문을 약 {abs(rain):.1f}%p 늘리는 쪽이어서, 날씨만으로는 이번 변화를 설명하기 어려워요."
        else:
            t = "비 온 날 수의 변화가 방문에 준 영향은 거의 없는 것으로 추정돼요."
        hyp.append(Sentence(text=t + " 가까운 기상 관측소 기준이라 동네 날씨와 조금 다를 수 있어요.",
                            claim_ids=[ev["region_rain_effect_pp"]]))
    to_check = [Sentence(text="같은 기간에 쉬는 날이나 가격·메뉴를 바꾼 적이 있다면 알려 주세요. 판단이 더 정확해져요.")]
    if note == "no_feasible":
        to_check.append(Sentence(text="지금 알려주신 예산·인력·바꿀 수 있는 항목 안에서 할 수 있는 실험을 찾지 못했어요. "
                                      "예산을 조금 늘리거나 바꿀 수 있는 항목(메뉴·영업시간·가격·판매 채널)을 더 알려주시면 다시 찾아 드릴게요."))
    return {"observed": observed, "my_store": my_store, "hypotheses": hyp, "to_check": to_check}


# ───────────────────────── LLM 다듬기 (decisions_C ③: 문장 수·순서·claim_id 는 코드가 고정)
class PolishedSentences(BaseModel):
    sentences: list[str]


def _llm_polish(texts: list[str], feedback: list[str]) -> list[str]:
    """템플릿 문장 목록을 같은 개수로 다듬어 돌려받는다. 실패는 모두 llm.LLMError."""
    prompt = (PROMPT_DIR / "polish_report.md").read_text(encoding="utf-8")
    prompt = (prompt.replace("{banned}", ", ".join(f"'{b}'" for b in BANNED_PHRASES))
                    .replace("{feedback}", "\n".join(feedback) or "(없음)")
                    .replace("{sentences}", json.dumps(texts, ensure_ascii=False, indent=1)))
    out = llm.extract_structured(prompt, PolishedSentences).sentences
    if len(out) != len(texts):
        raise llm.LLMError(f"문장 수가 다름: {len(out)} != {len(texts)}")
    return [o.strip() or t for o, t in zip(out, texts)]  # 빈 문장은 템플릿으로


def _polish(state, tmpl: list[str]) -> tuple[list[str], dict]:
    """처음: 전부 다듬기. 재시도: 위반 문장만 사유를 넣어 다시 다듬고, 마지막 재시도에서는 템플릿으로 되돌림."""
    retry = state.get("retries", 0)
    prev = (state.get("report") or {}).get("_polish")
    if retry and prev and prev["template"] == tmpl:
        texts = list(prev["texts"])
        vio = (state.get("verify_result") or {}).get("violations", [])
        bad = {i: [v["reason"] for v in vio if v["sentence"] == t] for i, t in enumerate(texts)}
        bad = {i: r for i, r in bad.items() if r}
        if not bad:
            return texts, {"action": "kept", "fixed": []}
        err = None
        if retry < MAX_RETRIES:
            fb = [f"{i + 1}번 문장 \"{texts[i]}\": {'; '.join(r)}" for i, r in bad.items()]
            try:
                new = _llm_polish(tmpl, fb)
                for i in bad:
                    texts[i] = new[i]
                return texts, {"action": "repolished", "fixed": sorted(bad)}
            except Exception as e:  # LLM 장애 → 해당 문장만 템플릿
                err = f"{type(e).__name__}: {e}"[:200]
        for i in bad:
            texts[i] = tmpl[i]
        return texts, {"action": "reverted", "fixed": sorted(bad), "error": err}
    try:
        return _llm_polish(tmpl, []), {"action": "polished"}
    except Exception as e:  # LLM 장애·형식 오류 → 템플릿
        return list(tmpl), {"action": "template", "error": f"{type(e).__name__}: {e}"[:200]}


def compose_report(state):
    """템플릿 리포트(점주 말투) → (LLM 사용 가능 시) 문장만 다듬기 → 재시도 때 verify 위반 사유를 반영."""
    si = StoreInput.model_validate(state["store_input"])
    ar = AnalysisResult.model_validate(state["analysis_result"])
    prev = state.get("report") or {}
    exps = [Experiment.model_validate(e) for e in prev.get("_experiments", [])]
    note = prev.get("_design_note")
    secs = _template_sections(si, ar, note)
    slots = [(sec, i) for sec in POLISH_SECTIONS for i in range(len(secs[sec]))]
    tmpl = [secs[sec][i].text for sec, i in slots]
    polish = None
    if state.get("llm_available", USE_LLM) and tmpl:
        texts, polish = _polish(state, tmpl)
        for (sec, i), t in zip(slots, texts):
            secs[sec][i] = Sentence(text=t, claim_ids=secs[sec][i].claim_ids)  # 근거 번호는 템플릿 것을 그대로
        polish |= {"template": tmpl, "texts": texts}
    if state.get("debug_inject") == "banned_phrase" and state.get("retries", 0) == 0:
        secs["hypotheses"].append(Sentence(text="매출 감소의 원인은 주변 경쟁 때문입니다."))  # 검증 루프 테스트용 결함
    progs = [p for p in json.loads((MOCK_DIR / "support_programs.json").read_text(encoding="utf-8"))
             if ar.diagnosis.value in p["fits"]]
    region_notice = ([Sentence(text="이번 답변은 동네 흐름만 알려 드리고, 내 가게 진단과 실험 추천은 하지 않았어요. "
                                    "그 이유는 아래 한계 항목에 적었어요.")] if note == "region_only" else [])
    rep = Report(
        mode=ar.mode, diagnosis=ar.diagnosis, **secs,
        experiment=exps[0] if exps else None, alternatives=exps[1:],
        support_programs=[Sentence(text=f"{p['title']} ({p['url']})") for p in progs],
        limits=region_notice
               + [Sentence(text="유동인구는 읍면동 전체를 집계한 값이고, 우리 가게 한 곳의 실험 전후 비교만으로는 "
                                "실험 효과를 확정할 수 없어요.")]
               + ([Sentence(text="실험의 예상 비용은 추정치예요. 점주님 상황에 맞게 조정해 주세요.")] if exps else [])
               + [Sentence(text=note_sentence(n)) for n in ar.notes]
               + ([Sentence(text="이 결과는 시연용 합성(가짜) 점주 데이터로 만들었어요.")] if si.is_synthetic else []),
        sources=[f"No.{f.dataset_no} {f.name} (기준일 {f.last_obs_date})" for f in ar.data_freshness if f.used]
                + [f"[analysis snapshot {ar.snapshot_id}]"],
        is_synthetic_input=si.is_synthetic,
    )
    out = rep.model_dump(mode="json")
    out["_experiments"] = [e.model_dump(mode="json") for e in exps]
    out["_design_note"] = note
    out["_polish"] = polish
    summary = {k: v for k, v in (polish or {}).items() if k not in ("template", "texts")} or None
    return {"report": out, "trace": log(state, "C.compose_report", retry=state.get("retries", 0), polish=summary)}


_NUM = re.compile(r"-?\d+(?:\.\d+)?(?=\s*%)")
SECTIONS = ["observed", "my_store", "hypotheses", "to_check", "limits"]


def _constraint_violations(rep: Report, ar: AnalysisResult, si: StoreInput) -> list[Violation]:
    """추천·대안 실험이 점주 조건을 지키는지 카탈로그 기준으로 다시 확인(설계 단계와 같은 규칙 재사용)."""
    cat = {x["id"]: x for x in _catalog()}
    exps = [x for x in [rep.experiment, *rep.alternatives] if x]
    out = []
    if exps and (ar.mode == Mode.region_only or ar.diagnosis == Diagnosis.undetermined):
        out.append(Violation(type="constraint_violation", section="experiment", sentence=exps[0].title,
                             reason="내 가게 진단이 없는데 실험을 추천함"))
    for x in exps:
        item = cat.get(x.catalog_id)
        if item is None:
            out.append(Violation(type="constraint_violation", section="experiment", sentence=x.title,
                                 reason=f"카탈로그에 없는 실험 {x.catalog_id}"))
            continue
        # 리포트에 적힌 비용·인력이 카탈로그와 다르게 바뀌었어도 큰 쪽 기준으로 검사
        item = item | {"cost_krw": max(item["cost_krw"], x.cost_krw), "extra_staff": max(item["extra_staff"], x.extra_staff)}
        for r in infeasible_reasons(item, si):
            out.append(Violation(type="constraint_violation", section="experiment", sentence=x.title, reason=r))
    return out


def verify(state):
    rep = Report.model_validate({k: v for k, v in state["report"].items() if not k.startswith("_")})
    ar = AnalysisResult.model_validate(state["analysis_result"])
    si = StoreInput.model_validate(state["store_input"])
    evd = {e.claim_id: e for e in ar.evidence}
    a_notes = {note_sentence(n) for n in ar.notes} | set(ar.notes)  # A 판정 사유: 금지 표현만 검사(decisions_C ⑥)
    vio: list[Violation] = []
    checked = 0
    for sec in SECTIONS:
        for s in getattr(rep, sec):
            for b in BANNED_PHRASES:
                if b in s.text:
                    vio.append(Violation(type="banned_phrase", section=sec, sentence=s.text, reason=f"금지 표현 '{b}'"))
            if sec == "limits" and s.text in a_notes:
                continue
            nums = _NUM.findall(s.text)
            if nums and not s.claim_ids:
                vio.append(Violation(type="missing_evidence", section=sec, sentence=s.text, reason="수치 문장에 근거 없음"))
            cited = []
            for cid in s.claim_ids:
                e = evd.get(cid)
                if e is None:
                    vio.append(Violation(type="missing_evidence", section=sec, sentence=s.text, reason=f"{cid} 없음"))
                    continue
                cited.append(e)
                checked += 1
                rv = recompute(e.metric, si.model_dump(mode="json"))  # A의 함수로 재계산
                if rv is None or abs(rv - e.value) > 0.05:
                    vio.append(Violation(type="number_mismatch", section=sec, sentence=s.text,
                                         reason=f"{cid}: 보고 {e.value} vs 재계산 {rv}"))
                if e.is_past_ref and "과거" not in s.text:
                    vio.append(Violation(type="past_as_current", section=sec, sentence=s.text, reason=f"{cid} 과거 참고 자료"))
            if cited:  # 문장에 적힌 숫자가 인용한 근거 값과 같은지(절댓값 비교, 방향은 '늘었/줄었'로 표현)
                for x in nums:
                    if not any(abs(abs(float(x)) - abs(e.value)) <= 0.05 for e in cited):
                        vals = ", ".join(str(e.value) for e in cited)
                        vio.append(Violation(type="number_mismatch", section=sec, sentence=s.text,
                                             reason=f"문장 속 {x}%가 인용한 근거 값({vals})과 다름"))
    vio += _constraint_violations(rep, ar, si)
    vr = VerifyResult(passed=not vio, violations=vio, checked_claims=checked)
    return {"verify_result": vr.model_dump(mode="json"),
            "trace": log(state, "C.verify", passed=vr.passed, n_violations=len(vio), checked=checked)}


def drop_violations(state):
    """재시도 한도 초과: 위반 문장 제거 + '확인 불가' 표기."""
    bad = {v["sentence"] for v in state["verify_result"]["violations"] if v["section"] != "experiment"}
    rep = dict(state["report"])
    for sec in SECTIONS:
        rep[sec] = [s for s in rep[sec] if s["text"] not in bad]
    bad_exp = {v["sentence"] for v in state["verify_result"]["violations"] if v["section"] == "experiment"}
    if bad_exp:  # 조건을 넘는 실험은 빼고, 남은 대안 중 첫 번째를 추천으로 올림
        keep = [e for e in [rep.get("experiment"), *rep.get("alternatives", [])] if e and e["title"] not in bad_exp]
        rep["experiment"], rep["alternatives"] = (keep[0] if keep else None), keep[1:]
        rep["_experiments"] = keep
    rep["limits"] = rep["limits"] + [{"text": f"검증을 통과하지 못한 문장 {len(bad)}개를 제외했습니다(확인 불가).", "claim_ids": []}]
    return {"report": rep, "status": "done_with_drops", "trace": log(state, "C.drop_violations", dropped=len(bad))}
