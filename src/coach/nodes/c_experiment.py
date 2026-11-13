"""★C 실험 설계·검증 — design_experiment / compose_report / verify / drop_violations.

mock 모드: 카탈로그 필터·랭킹 + 템플릿 문장. 실제 모드 TODO: LLM이 근거(claim_id)만 인용해 문장 다듬기.
"""
from __future__ import annotations

import json
import re

import yaml

from ..config import BANNED_PHRASES, MOCK_DIR
from ..contracts import AnalysisResult, Experiment, Report, Sentence, StoreInput, VerifyResult, Violation
from ..trace import log
from .a_data import recompute

DIAG_KO = {"co_decline": "동네 동반 하락형", "only_me": "나만 하락형",
           "season_weather": "계절·날씨형", "undetermined": "판단 불가"}


def _catalog():
    return yaml.safe_load((MOCK_DIR / "catalog.yaml").read_text(encoding="utf-8"))


def design_experiment(state):
    si = StoreInput.model_validate(state["store_input"])
    ar = AnalysisResult.model_validate(state["analysis_result"])
    budget = si.budget_krw or 0
    can = {c.value for c in si.changeable}
    cands = [x for x in _catalog()
             if ar.diagnosis.value in x["fits"] and x["cost_krw"] <= budget
             and x["extra_staff"] == 0 and set(x["requires"]) <= can]  # MVP: 추가 인력 불가 가정
    cands.sort(key=lambda x: (x["fits"].index(ar.diagnosis.value), x["cost_krw"]))  # TODO(C): 랭킹 모델/LLM 재순위
    exps = [Experiment(catalog_id=x["id"], title=x["title"], cost_krw=x["cost_krw"], extra_staff=x["extra_staff"],
                       metric=x["metric"], stop_rule=x["stop_rule"],
                       rationale=Sentence(text=f"{DIAG_KO[ar.diagnosis.value]}에 맞고 예산·인력 조건을 넘지 않습니다."))
            for x in cands[:3]]
    rep = dict(state.get("report") or {})
    rep["_experiments"] = [e.model_dump(mode="json") for e in exps]
    return {"report": rep, "trace": log(state, "C.design_experiment", candidates=[e.catalog_id for e in exps])}


def compose_report(state):
    """템플릿 리포트. 재시도 시 verify 위반 사유를 반영(TODO(C): LLM에게 violations 피드백)."""
    si = StoreInput.model_validate(state["store_input"])
    ar = AnalysisResult.model_validate(state["analysis_result"])
    m, ev = ar.metrics, {e.metric: e.claim_id for e in ar.evidence}
    exps = [Experiment.model_validate(e) for e in (state.get("report") or {}).get("_experiments", [])]
    dong = si.dong_name or "해당 지역"
    observed, my_store, hyp = [], [], []
    if "region_visit_yoy_pct" in m:
        observed.append(Sentence(text=f"{dong}의 방문인구는 직전 기간 대비 {m['region_visit_prev_pct']}%, 전년 동기 대비 "
                                      f"{m['region_visit_yoy_pct']}% 변했습니다. 읍면동 전체 수치이며 매장 방문자 수와 다릅니다.",
                                 claim_ids=[ev["region_visit_prev_pct"], ev["region_visit_yoy_pct"]]))
        hyp.append(Sentence(text=f"강수 효과는 약 {m['region_rain_effect_pp']}%p로 추정됩니다(관측소 대표성 한계).",
                            claim_ids=[ev["region_rain_effect_pp"]]))
    if m.get("my_orders_change_pct") is not None:
        my_store.append(Sentence(text=f"내 가게 영업일당 주문은 {m['my_orders_change_pct']}% 변했습니다 → "
                                      f"{DIAG_KO[ar.diagnosis.value]}(근거 강도: {ar.evidence_strength})",
                                 claim_ids=[ev["my_orders_change_pct"]]))
    if state.get("debug_inject") == "banned_phrase" and state.get("retries", 0) == 0:
        hyp.append(Sentence(text="매출 감소의 원인은 주변 경쟁 때문입니다."))  # 검증 루프 테스트용 결함
    progs = [p for p in json.loads((MOCK_DIR / "support_programs.json").read_text(encoding="utf-8"))
             if ar.diagnosis.value in p["fits"]]
    rep = Report(
        mode=ar.mode, diagnosis=ar.diagnosis, observed=observed, my_store=my_store, hypotheses=hyp,
        to_check=[Sentence(text="같은 기간 영업일·가격·메뉴 변경이 있었는지 알려주시면 판단이 더 정확해집니다.")],
        experiment=exps[0] if exps else None, alternatives=exps[1:],
        support_programs=[Sentence(text=f"{p['title']} ({p['url']})") for p in progs],
        limits=[Sentence(text="유동인구는 읍면동 단위 집계이며, 단일 점포 전후 비교로 실험 효과를 확정할 수 없습니다.")]
               + [Sentence(text=n) for n in ar.notes]
               + ([Sentence(text="이 결과는 합성(가짜) 점주 데이터로 만든 시연입니다.")] if si.is_synthetic else []),
        sources=[f"No.{f.dataset_no} {f.name} (기준일 {f.last_obs_date})" for f in ar.data_freshness if f.used]
                + [f"[analysis snapshot {ar.snapshot_id}]"],
        is_synthetic_input=si.is_synthetic,
    )
    out = rep.model_dump(mode="json")
    out["_experiments"] = [e.model_dump(mode="json") for e in exps]
    return {"report": out, "trace": log(state, "C.compose_report", retry=state.get("retries", 0))}


_NUM = re.compile(r"-?\d+(?:\.\d+)?(?=\s*%)")
SECTIONS = ["observed", "my_store", "hypotheses", "to_check", "limits"]


def verify(state):
    rep = Report.model_validate({k: v for k, v in state["report"].items() if not k.startswith("_")})
    ar = AnalysisResult.model_validate(state["analysis_result"])
    si = StoreInput.model_validate(state["store_input"])
    evd = {e.claim_id: e for e in ar.evidence}
    vio: list[Violation] = []
    checked = 0
    for sec in SECTIONS:
        for s in getattr(rep, sec):
            for b in BANNED_PHRASES:
                if b in s.text:
                    vio.append(Violation(type="banned_phrase", section=sec, sentence=s.text, reason=f"금지 표현 '{b}'"))
            nums = _NUM.findall(s.text)
            if nums and not s.claim_ids:
                vio.append(Violation(type="missing_evidence", section=sec, sentence=s.text, reason="수치 문장에 근거 없음"))
            for cid in s.claim_ids:
                e = evd.get(cid)
                if e is None:
                    vio.append(Violation(type="missing_evidence", section=sec, sentence=s.text, reason=f"{cid} 없음"))
                    continue
                checked += 1
                rv = recompute(e.metric, si.model_dump(mode="json"))  # A의 함수로 재계산
                if rv is None or abs(rv - e.value) > 0.05:
                    vio.append(Violation(type="number_mismatch", section=sec, sentence=s.text,
                                         reason=f"{cid}: 보고 {e.value} vs 재계산 {rv}"))
                if e.is_past_ref and "과거" not in s.text:
                    vio.append(Violation(type="past_as_current", section=sec, sentence=s.text, reason=f"{cid} 과거 참고 자료"))
    for x in [rep.experiment, *rep.alternatives]:
        if x and (x.cost_krw > (si.budget_krw or 0) or x.extra_staff > 0):
            vio.append(Violation(type="constraint_violation", section="experiment", sentence=x.title, reason="예산·인력 초과"))
    vr = VerifyResult(passed=not vio, violations=vio, checked_claims=checked)
    return {"verify_result": vr.model_dump(mode="json"),
            "trace": log(state, "C.verify", passed=vr.passed, n_violations=len(vio), checked=checked)}


def drop_violations(state):
    """재시도 한도 초과: 위반 문장 제거 + '확인 불가' 표기."""
    bad = {v["sentence"] for v in state["verify_result"]["violations"]}
    rep = dict(state["report"])
    for sec in SECTIONS:
        rep[sec] = [s for s in rep[sec] if s["text"] not in bad]
    rep["limits"] = rep["limits"] + [{"text": f"검증을 통과하지 못한 문장 {len(bad)}개를 제외했습니다(확인 불가).", "claim_ids": []}]
    return {"report": rep, "status": "done_with_drops", "trace": log(state, "C.drop_violations", dropped=len(bad))}
