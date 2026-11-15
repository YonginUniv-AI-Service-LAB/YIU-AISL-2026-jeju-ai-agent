"""★A 데이터·분석 에이전트 — check_fitness / analyze(+classify) / recompute.

mock 모드: mocks/region_mock.json 의 가짜 지역 지표 + 점주 입력 실적으로 '실제로' 계산한다.
실제 모드(COACH_MOCK=0): data/snapshot/<기준일>/ 스냅샷에서 data/region_real.py 가 계산 → 같은 모양 dict.
"""
from __future__ import annotations

import json
from functools import lru_cache
from statistics import mean

from ..config import MOCK_DIR, THRESHOLDS, USE_MOCK
from ..contracts import AnalysisResult, Diagnosis, Evidence, Freshness, Mode, StoreInput
from ..trace import log


def dong_codes() -> dict[str, str]:
    """{읍면동 이름: 행정동코드} (행정안전부 KIKmix 기준, '_' 메모 키 제외). mock·실제 공용."""
    d = json.loads((MOCK_DIR / "dong_codes.json").read_text(encoding="utf-8"))
    return {k: v for k, v in d.items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def _real_region() -> dict:
    from ..data.region_real import build_region  # pandas 는 실제 모드에서만 불러옴
    return build_region(dong_codes())


def _region():
    if USE_MOCK:
        return json.loads((MOCK_DIR / "region_mock.json").read_text(encoding="utf-8"))
    return _real_region()  # 한 실행 안에서는 같은 스냅샷 → 한 번만 계산


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
    notes = list(reg.get("excluded", []))  # 실제 모드: 결측 과다로 뺀 읍면동 사유
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
        rain_src = 756 if USE_MOCK else 15059093   # 실제 모드: 756 멈춤 → 기상청 ASOS(노션 decisions 6)
        ev = [
            Evidence(claim_id="E01", metric="region_visit_prev_pct", value=d["visit_prev_pct"], unit="pct",
                     formula="(최근6주 일평균 내국인 방문 유동인구 − 직전6주) / 직전6주 × 100", source_nos=[753],
                     period=f"{w['recent']} vs {w['prev']}"),
            Evidence(claim_id="E02", metric="region_visit_yoy_pct", value=d["visit_yoy_pct"], unit="pct",
                     formula="(최근6주 일평균 내국인 방문 유동인구 − 전년동기6주) / 전년동기6주 × 100", source_nos=[753],
                     period=f"{w['recent']} vs {w['yoy']}"),
            Evidence(claim_id="E03", metric="region_rain_effect_pp", value=d["rain_effect_pp"], unit="pp",
                     formula="−(강수일 평균 − 비강수일 평균)/비강수일 평균 × (최근 강수일 비율 − 직전 강수일 비율) × 100, "
                             "강수일 = 일강수량 ≥ 1mm", source_nos=[753, rain_src],
                     period=f"{w['recent']} vs {w['prev']}"),
        ]
        if my is not None:
            ev.append(Evidence(claim_id="E04", metric="my_orders_change_pct", value=my, unit="pct",
                               formula="(최근6주 영업일당 주문 − 직전6주) / 직전6주 × 100", source_nos=[0],
                               period=f"{w['recent']} vs {w['prev']}"))
        # 보조 지표: 외국인 단기체류(753과 합산하지 않음, 노션 decisions 5). 지역 데이터에 있을 때만
        for cid, key, base in (("E05", "visit_for_prev_pct", "prev"), ("E06", "visit_for_yoy_pct", "yoy")):
            if d.get(key) is not None:
                metrics[f"region_{key}"] = d[key]
                ev.append(Evidence(claim_id=cid, metric=f"region_{key}", value=d[key], unit="pct",
                                   formula="(최근6주 일평균 외국인 단기체류 유동인구 − {0}) / {0} × 100".format(
                                       "직전6주" if base == "prev" else "전년동기6주"), source_nos=[748],
                                   period=f"{w['recent']} vs {w[base]}"))
    ar = AnalysisResult(mode=part["mode"], snapshot_id=reg["snapshot_id"], data_freshness=part["data_freshness"],
                        metrics=metrics, diagnosis=diag, evidence_strength=strength, evidence=ev, notes=part["notes"])
    return {"analysis_result": ar.model_dump(mode="json"),
            "trace": log(state, "A.analyze", diagnosis=diag.value, metrics=metrics)}
