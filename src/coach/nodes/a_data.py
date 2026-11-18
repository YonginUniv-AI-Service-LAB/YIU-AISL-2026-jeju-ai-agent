"""★A 데이터·분석 에이전트 — check_fitness / analyze(+classify) / recompute.

mock 모드: mocks/region_mock.json 의 가짜 지역 지표 + 점주 입력 실적으로 '실제로' 계산한다.
스냅샷 모드(COACH_MOCK=0): data/snapshot/<기준일>/ 스냅샷에서 data/region_real.py 가 계산 → 같은 모양 dict. (재현·테스트 기준)
라이브 모드(COACH_MOCK=0 COACH_LIVE=1): 점주 주문 기록 시기에 맞춰 API로 동네 흐름 조회(data/region_live.py),
  실패하면 스냅샷으로 대체. 계산 함수는 스냅샷 모드와 같다.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from functools import lru_cache
from statistics import mean

from ..config import MOCK_DIR, THRESHOLDS, USE_MOCK
from ..data import fitness as FIT
from ..data.fitness import MIN_STORE_WEEKS, STORE_WEEKS  # 기준값은 fitness.py 한 곳(decisions ②-주수)
from ..contracts import AnalysisResult, Diagnosis, Evidence, Freshness, StoreInput
from ..trace import log


def dong_codes() -> dict[str, str]:
    """{읍면동 이름: 행정동코드} (행정안전부 KIKmix 기준, '_' 메모 키 제외). mock·실제 공용."""
    d = json.loads((MOCK_DIR / "dong_codes.json").read_text(encoding="utf-8"))
    return {k: v for k, v in d.items() if not k.startswith("_")}


def _no_region(reason: str) -> dict:
    """지역 데이터를 쓸 수 없을 때도 멈추지 않도록 같은 모양의 빈 지역 dict(→ F4 판정 불가 + 준비 방법 안내)."""
    return {"snapshot_id": "none", "window": {"recent": "", "prev": "", "yoy": ""}, "dongs": {}, "freshness": [],
            "as_of": None, "excluded": [f"[판정 불가] {reason}"]}


@lru_cache(maxsize=1)
def _real_region() -> dict:
    from ..data.region_real import build_region  # pandas 는 실제 모드에서만 불러옴
    try:
        return build_region(dong_codes())
    except FileNotFoundError:   # 스냅샷 폴더가 없음(제출물에 없거나 아직 안 만듦)
        return _no_region("기준일 지역 데이터(스냅샷)가 없어 지역 지표로 진단할 수 없음 → 준비 방법은 "
                          "실행 매뉴얼 참고(스냅샷 폴더 넣기 또는 데이터 키로 라이브 모드)")
    except ValueError:          # manifest 해시 불일치(변조·손상)
        return _no_region("기준일 지역 데이터(스냅샷)가 원본과 달라(해시 불일치) 쓰지 않음 → 스냅샷을 다시 받아 주세요")


USE_LIVE = os.getenv("COACH_LIVE", "0") == "1"   # ★A 라이브 모드 스위치(COACH_MOCK=0 일 때만 의미)


@lru_cache(maxsize=32)
def _live_region(dong_code: str, target_iso: str) -> dict:
    """같은 읍면동·같은 기준일은 한 번만 조회(check_fitness·analyze·C 의 recompute 가 같은 숫자를 보게)."""
    from ..data import region_live as L
    weeks_stub = [{"week_start": str(date.fromisoformat(target_iso) - timedelta(days=6))}]
    return L.region_for({"dong_code": dong_code or None, "weeks": weeks_stub}, dong_codes(), _real_region)


def _region(store_input: dict | None = None):
    """지역 지표 dict(모양 고정). 라이브 모드에서는 점주 입력(store_input)의 읍면동·기간에 맞춰 조회한다."""
    if USE_MOCK:
        return json.loads((MOCK_DIR / "region_mock.json").read_text(encoding="utf-8"))
    if USE_LIVE and store_input is not None:
        from ..data.region_live import target_date
        return _live_region(store_input.get("dong_code") or "", str(target_date(store_input)))
    return _real_region()  # 한 실행 안에서는 같은 스냅샷 → 한 번만 계산


# ── 순수 함수: C의 검증 에이전트도 이 함수로 재계산한다(중복 구현 금지)
def pct_change(recent: float, base: float) -> float:
    return round((recent - base) / base * 100, 1) if base else 0.0


def store_compare_weeks(weeks: list[dict]) -> tuple[list[dict], list[dict]] | None:
    """가장 최근 12주를 (직전 6주, 최근 6주)로. 12주가 안 되면 None. 16주를 넣어도 최근 12주만 쓴다."""
    w = sorted(weeks, key=lambda x: x["week_start"])[-MIN_STORE_WEEKS:]
    if len(w) < MIN_STORE_WEEKS:
        return None
    return w[:STORE_WEEKS], w[STORE_WEEKS:]


def my_orders_change_pct(weeks: list[dict]) -> float | None:
    """최근 6주 vs 직전 6주의 영업일당 주문 평균 변화율(%). 동네 창과 같은 6주 vs 6주로 고정."""
    split = store_compare_weeks(weeks)
    if split is None:
        return None
    prev, recent = ([x["orders"] / x["open_days"] for x in part if x["open_days"]] for part in split)
    if not prev or not recent:      # 한쪽 6주가 전부 휴업이면 비교 불가
        return None
    return pct_change(mean(recent), mean(prev))


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


def reachable_diagnoses(visit_prev: float, visit_yoy: float, rain_pp: float) -> set[str]:
    """이 동네·기준일의 지역 숫자에서 '가게 주문 변화가 어떤 값이든' 나올 수 있는 진단 유형.
    평가용(S9): 기대 진단이 여기 없으면 그 사례는 실데이터로 '평가 불가'(가게 결과는 보지 않음 → 정답 누수 없음).
    내 주문 변화 −100%~+100%를 0.1%p 간격으로 대입한다(classify 는 구간 비교라 이 간격이면 충분)."""
    return {classify(x / 10, visit_prev, visit_yoy, rain_pp)[0].value for x in range(-1000, 1001)}


def recompute(metric: str, store_input: dict) -> float | None:
    """검증용 재계산 진입점(C가 호출)."""
    if metric == "my_orders_change_pct":
        return my_orders_change_pct(store_input.get("weeks", []))
    d = _region(store_input)["dongs"].get(store_input.get("dong_code") or "", {})   # 라이브면 같은 조회 결과(캐시)
    return d.get(metric.replace("region_", ""))


def store_period_label(weeks: list[dict]) -> str | None:
    """근거 E04 의 기간: 가게 비교에 실제로 쓴 '직전 6주 vs 최근 6주' 날짜(동네 창 날짜와 다를 수 있음)."""
    split = store_compare_weeks(weeks)
    if split is None:
        return None
    def span(part):
        a = date.fromisoformat(str(part[0]["week_start"]))
        return f"{a}~{date.fromisoformat(str(part[-1]['week_start'])) + timedelta(days=6)}"
    return f"{span(split[1])} vs {span(split[0])}"


# ── 노드
def _judge(state) -> FIT.Fitness:
    """적합성 판정(순수 함수 fitness.judge). check_fitness·analyze 가 같은 입력으로 다시 불러도 같은 결과."""
    reg = _region(state["store_input"])
    as_of = date.fromisoformat(reg["as_of"]) if reg.get("as_of") else None   # mock 은 기준일 없음 → F5 건너뜀
    return FIT.judge(state["store_input"], state.get("question", ""), reg, as_of)


def _region_source(reg: dict) -> str:
    """trace 표시용 지역 데이터 출처. 라이브 조회가 실패해 스냅샷으로 대체됐으면 그렇게 표시(#10 리뷰)."""
    if USE_MOCK:
        return "mock"
    live = reg.get("live") or {}
    if live.get("fallback"):
        return "snapshot_fallback"
    return "live" if live else "snapshot"


def check_fitness(state):
    """데이터 적합성 판정(F1~F7) → 적합 / 범위 축소 / 판정 불가 + 모드 + 사유. 규칙: src/coach/data/fitness.py"""
    StoreInput.model_validate(state["store_input"])
    reg = _region(state["store_input"])
    fresh = [Freshness.model_validate(f) for f in reg["freshness"]]
    fit = _judge(state)
    notes = list(reg.get("excluded", [])) + fit.notes  # 실제 모드: 결측 과다로 뺀 읍면동 사유 + 판정 사유
    partial = {"mode": fit.mode, "data_freshness": [f.model_dump(mode="json") for f in fresh], "notes": notes}
    return {"analysis_result": partial,
            "trace": log(state, "A.check_fitness", fitness=fit.verdict, cases=fit.cases, details=fit.details, mode=fit.mode, notes=notes,
                         region_source=_region_source(reg),
                         snapshot_id=reg.get("snapshot_id"), live=reg.get("live"))}


def analyze(state):
    si = StoreInput.model_validate(state["store_input"])
    part = state["analysis_result"]
    reg = _region(state["store_input"])
    d = reg["dongs"].get(si.dong_code or "", None)
    fit = _judge(state)
    weeks = [w.model_dump(mode="json") for w in si.weeks]
    my = my_orders_change_pct(weeks) if fit.store_compare else None   # F2·F3·F7 → 내 가게 비교 안 함
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
                               period=store_period_label(weeks)))
        # 보조 지표: 외국인 단기체류(753과 합산하지 않음, 노션 decisions 5). 지역 데이터에 있고 F6 이 아닐 때만
        for cid, key, base in (("E05", "visit_for_prev_pct", "prev"), ("E06", "visit_for_yoy_pct", "yoy")):
            if fit.use_foreign and d.get(key) is not None:
                metrics[f"region_{key}"] = d[key]
                ev.append(Evidence(claim_id=cid, metric=f"region_{key}", value=d[key], unit="pct",
                                   formula="(최근6주 일평균 외국인 단기체류 유동인구 − {0}) / {0} × 100".format(
                                       "직전6주" if base == "prev" else "전년동기6주"), source_nos=[748],
                                   period=f"{w['recent']} vs {w[base]}"))
    ar = AnalysisResult(mode=part["mode"], snapshot_id=reg["snapshot_id"], data_freshness=part["data_freshness"],
                        metrics=metrics, diagnosis=diag, evidence_strength=strength, evidence=ev, notes=part["notes"])
    return {"analysis_result": ar.model_dump(mode="json"),
            "trace": log(state, "A.analyze", diagnosis=diag.value, metrics=metrics)}
