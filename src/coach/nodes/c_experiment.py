"""★C 실험 설계·검증 — design_experiment / compose_report / verify / drop_violations.

mock 모드: 카탈로그 필터·랭킹 + 템플릿 문장. 실제 모드 TODO: LLM이 근거(claim_id)만 인용해 문장 다듬기.
"""
from __future__ import annotations

import json
import re

import yaml

from ..config import BANNED_PHRASES, MOCK_DIR
from ..contracts import (AnalysisResult, Diagnosis, Experiment, Mode, Report, Sentence, StoreInput, VerifyResult,
                         Violation)
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


def compose_report(state):
    """템플릿 리포트. 재시도 시 verify 위반 사유를 반영(TODO(C): LLM에게 violations 피드백)."""
    si = StoreInput.model_validate(state["store_input"])
    ar = AnalysisResult.model_validate(state["analysis_result"])
    m, ev = ar.metrics, {e.metric: e.claim_id for e in ar.evidence}
    prev = state.get("report") or {}
    exps = [Experiment.model_validate(e) for e in prev.get("_experiments", [])]
    note = prev.get("_design_note")
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
    to_check = [Sentence(text="같은 기간 영업일·가격·메뉴 변경이 있었는지 알려주시면 판단이 더 정확해집니다.")]
    if note == "no_feasible":
        to_check.append(Sentence(text="지금 알려주신 예산·인력·바꿀 수 있는 항목 안에서 할 수 있는 실험을 찾지 못했습니다. "
                                      "예산을 조금 늘리거나 바꿀 수 있는 항목(메뉴·영업시간·가격·판매 채널)을 더 알려주시면 다시 찾아 드릴게요."))
    region_notice = ([Sentence(text="이번 답변은 동네 흐름만 알려 드리며, 내 가게 진단과 실험 추천은 하지 않았습니다. "
                                    "그 이유는 아래 한계 항목에 적었습니다.")] if note == "region_only" else [])
    rep = Report(
        mode=ar.mode, diagnosis=ar.diagnosis, observed=observed, my_store=my_store, hypotheses=hyp,
        to_check=to_check,
        experiment=exps[0] if exps else None, alternatives=exps[1:],
        support_programs=[Sentence(text=f"{p['title']} ({p['url']})") for p in progs],
        limits=region_notice
               + [Sentence(text="유동인구는 읍면동 단위 집계이며, 단일 점포 전후 비교로 실험 효과를 확정할 수 없습니다.")]
               + ([Sentence(text="실험의 예상 비용은 추정치이며, 점주님 상황에 맞게 조정해 주세요.")] if exps else [])
               + [Sentence(text=n) for n in ar.notes]
               + ([Sentence(text="이 결과는 합성(가짜) 점주 데이터로 만든 시연입니다.")] if si.is_synthetic else []),
        sources=[f"No.{f.dataset_no} {f.name} (기준일 {f.last_obs_date})" for f in ar.data_freshness if f.used]
                + [f"[analysis snapshot {ar.snapshot_id}]"],
        is_synthetic_input=si.is_synthetic,
    )
    out = rep.model_dump(mode="json")
    out["_experiments"] = [e.model_dump(mode="json") for e in exps]
    out["_design_note"] = note
    return {"report": out, "trace": log(state, "C.compose_report", retry=state.get("retries", 0))}


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
    vio += _constraint_violations(rep, ar, si)
    vr = VerifyResult(passed=not vio, violations=vio, checked_claims=checked)
    return {"verify_result": vr.model_dump(mode="json"),
            "trace": log(state, "C.verify", passed=vr.passed, n_violations=len(vio), checked=checked)}


def drop_violations(state):
    """재시도 한도 초과: 위반 문장 제거 + '확인 불가' 표기."""
    bad = {v["sentence"] for v in state["verify_result"]["violations"]}
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
