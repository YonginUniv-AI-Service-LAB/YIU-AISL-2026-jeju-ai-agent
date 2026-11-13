"""★A 데이터·분석 에이전트 — check_fitness / analyze(+classify) / recompute.

mock 모드: mocks/region_mock.json 의 가짜 지역 지표 + 점주 입력 실적으로 '실제로' 계산한다.
실제 모드 TODO: No.753·748·756 스냅샷(DuckDB) 조회 → 같은 metrics 키로 반환.
"""
from __future__ import annotations

import json
from statistics import mean

from ..config import MOCK_DIR, THRESHOLDS, USE_MOCK
from ..contracts import AnalysisResult, Diagnosis, Evidence, Freshness, Mode, StoreInput
from ..trace import log


def _region():
    if USE_MOCK:
        return json.loads((MOCK_DIR / "region_mock.json").read_text(encoding="utf-8"))
    raise NotImplementedError("TODO(A): 스냅샷에서 읍면동 지표 계산")


# ── 순수 함수: C의 검증 에이전트도 이 함수로 재계산한다(중복 구현 금지)
def pct_change(recent: float, base: float) -> float:
    return round((recent - base) / base * 100, 1) if base else 0.0


def my_orders_change_pct(weeks: list[dict]) -> float | None:
    """최근 6주 평균 vs 직전 6주 평균(영업일 보정: 주문/영업일)."""
    if len(weeks) < 8:
        return None
    w = sorted(weeks, key=lambda x: x["week_start"])
    per_day = [x["orders"] / x["open_days"] for x in w if x["open_days"]]
    half = len(per_day) // 2
    return pct_change(mean(per_day[half:]), mean(per_day[:half]))


def classify(my: float | None, visit_prev: float, visit_yoy: float, rain_pp: float) -> tuple[Diagnosis, str]:
    """규칙 기반 진단(결정적). 임계값은 config.THRESHOLDS."""
    t = THRESHOLDS
    if my is None:
        return Diagnosis.undetermined, "약"
    if my > t["my_decline_pct"]:
        return Diagnosis.undetermined, "약"  # 뚜렷한 하락 아님
    if visit_prev <= t["my_decline_pct"] and visit_yoy > t["region_flat_pct"]:
        return Diagnosis.season_weather, "중" if rain_pp >= 3 else "약"
    if visit_yoy <= t["region_flat_pct"] and abs(my - visit_prev) <= t["co_decline_gap_pp"]:
        return Diagnosis.co_decline, "중"
    if visit_prev > t["region_flat_pct"]:
        return Diagnosis.only_me, "중"
    return Diagnosis.undetermined, "약"


def recompute(metric: str, store_input: dict) -> float | None:
    """검증용 재계산 진입점(C가 호출)."""
    if metric == "my_orders_change_pct":
        return my_orders_change_pct(store_input.get("weeks", []))
    d = _region()["dongs"].get(store_input.get("dong_code") or "", {})
    return d.get(metric.replace("region_", ""))


# ── 노드
def check_fitness(state):
    """데이터 적합성 판정 → 모드 결정. TODO(A): LLM이 질문 의도에 맞는 데이터 후보를 고르게 확장."""
    si = StoreInput.model_validate(state["store_input"])
    reg = _region()
    fresh = [Freshness.model_validate(f) for f in reg["freshness"]]
    notes = []
    if si.dong_code not in reg["dongs"]:
        notes.append(f"{si.dong_name or si.dong_code}: 시범 읍면동 밖 → 지역 지표 없음")
    if not si.weeks:
        mode = Mode.region_only
        notes.append("점주 실적 없음 → '지역 수요 참고 분석'으로 강등")
    else:
        mode = Mode.current
    partial = {"mode": mode.value, "data_freshness": [f.model_dump(mode="json") for f in fresh], "notes": notes}
    return {"analysis_result": partial, "trace": log(state, "A.check_fitness", mode=mode.value, notes=notes)}


def analyze(state):
    si = StoreInput.model_validate(state["store_input"])
    part = state["analysis_result"]
    reg = _region()
    d = reg["dongs"].get(si.dong_code or "", None)
    my = my_orders_change_pct([w.model_dump(mode="json") for w in si.weeks])
    if d is None:
        diag, strength = Diagnosis.undetermined, "약"
        metrics, ev = {"my_orders_change_pct": my}, []
    else:
        diag, strength = classify(my, d["visit_prev_pct"], d["visit_yoy_pct"], d["rain_effect_pp"])
        metrics = {"region_visit_prev_pct": d["visit_prev_pct"], "region_visit_yoy_pct": d["visit_yoy_pct"],
                   "region_rain_effect_pp": d["rain_effect_pp"], "my_orders_change_pct": my}
        w = reg["window"]
        ev = [
            Evidence(claim_id="E01", metric="region_visit_prev_pct", value=d["visit_prev_pct"], unit="pct",
                     formula="(최근6주 일평균 방문인구 − 직전6주) / 직전6주 × 100", source_nos=[753, 748],
                     period=f"{w['recent']} vs {w['prev']}"),
            Evidence(claim_id="E02", metric="region_visit_yoy_pct", value=d["visit_yoy_pct"], unit="pct",
                     formula="(최근6주 − 전년동기6주) / 전년동기6주 × 100", source_nos=[753, 748],
                     period=f"{w['recent']} vs {w['yoy']}"),
            Evidence(claim_id="E03", metric="region_rain_effect_pp", value=d["rain_effect_pp"], unit="pp",
                     formula="강수일·비강수일 방문인구 차이를 강수일 비중으로 가중", source_nos=[753, 756],
                     period=w["recent"]),
        ]
        if my is not None:
            ev.append(Evidence(claim_id="E04", metric="my_orders_change_pct", value=my, unit="pct",
                               formula="(최근6주 영업일당 주문 − 직전6주) / 직전6주 × 100", source_nos=[0],
                               period=f"{w['recent']} vs {w['prev']}"))
    ar = AnalysisResult(mode=part["mode"], snapshot_id=reg["snapshot_id"], data_freshness=part["data_freshness"],
                        metrics=metrics, diagnosis=diag, evidence_strength=strength, evidence=ev, notes=part["notes"])
    return {"analysis_result": ar.model_dump(mode="json"),
            "trace": log(state, "A.analyze", diagnosis=diag.value, metrics=metrics)}
