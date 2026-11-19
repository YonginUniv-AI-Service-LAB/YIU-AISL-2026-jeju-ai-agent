"""★A 실데이터 지역 지표 계산 (COACH_MOCK=0). 스냅샷(parquet)만 읽고 API는 부르지 않는다.

결정 근거는 노션 A파트 decisions (2026-11-14), 데이터 명세는 노션 A파트 data_spec.
- 진단 지표: No.753 내국인 **방문**(visit_pop) 일 합계의 창 평균 변화율        → visit_prev_pct, visit_yoy_pct
- 보조 지표: No.748 외국인 단기체류 일 합계의 창 평균 변화율(753과 합산하지 않음) → visit_for_prev_pct, visit_for_yoy_pct
- 강수 보정: 기상청 ASOS 일강수량 ≥ 1mm 를 강수일로 보고 rain_effect_pp 계산
- 결측: 빠진 날(행이 모자란 날 포함)은 제외, 0으로 채우지 않음. 창 안 결측 > 10% 면 그 읍면동은 부적합

주의(금지 해석): 값은 읍면동 단위 "유동인구 일 합계"이며 매장 방문객·고유 방문자·관광객 수가 아니다.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from .fitness import FOREIGN_GAP_MAX_RATIO, LAG_WARN_DAYS  # noqa: F401  (기준값은 fitness.py 한 곳, decisions ①-2·①-3)

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT_ROOT = ROOT / "data" / "snapshot"


def last_sunday(d: date) -> date:
    """d 가 일요일이면 d, 아니면 그 직전 일요일(창을 월~일 주 단위로 맞추기 위함)."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


# ── 실제 모드 설정(★A, 노션 decisions 와 같이 고칠 것)
# 기준일은 코드에 흩어 두지 않고 여기 한 곳에서만 정한다. 바꾸려면 환경변수 COACH_AS_OF=YYYY-MM-DD
AS_OF = date.fromisoformat(os.getenv("COACH_AS_OF", "2026-08-31"))   # 기본: 753·748 실제 마지막 관측일
RECENT_END = last_sunday(AS_OF)      # 최근 창 끝(일요일) → 6주 = 월~일 × 6  (2026-08-31 → 2026-08-30)
WEEKS = 6
YOY_SHIFT_DAYS = 364                 # 52주 전 → 요일 구성 동일
RAIN_MM = 1.0                        # 강수일 기준
MAX_MISSING_RATIO = 0.10             # 창 안 결측 허용 비율 (42일 → 4일) — validate 실패 기준과 동일
STATION_OF = {"애월읍": "184", "노형동": "184", "연동": "184", "한림읍": "185"}  # 가장 가까운 ASOS 관측소

# S1 조사로 확인한 사실(스냅샷에 들어가지 않는 데이터셋). 조사일 2026-11-14, 노션 A파트 data_spec
SURVEY = {
    756: {"name": "일별 기상 정보", "last_obs_date": "2025-03-30", "unit": "관측소/일", "used": False,
          "reason": "2025-03-30 이후 관측 없음(조사일 2026-11-14) → 기상청 ASOS로 대체"},
}
ASOS_LATEST_AT_SURVEY = "2026-11-12"


# ── 창(기간)
@dataclass(frozen=True)
class Window:
    start: date
    end: date

    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]

    def label(self) -> str:
        return f"{self.start}~{self.end}"


def windows(recent_end: date = RECENT_END, weeks: int = WEEKS, yoy_shift: int = YOY_SHIFT_DAYS) -> dict[str, Window]:
    n = 7 * weeks
    recent = Window(recent_end - timedelta(days=n - 1), recent_end)
    prev = Window(recent.start - timedelta(days=n), recent.start - timedelta(days=1))
    yoy = Window(recent.start - timedelta(days=yoy_shift), recent.end - timedelta(days=yoy_shift))
    return {"recent": recent, "prev": prev, "yoy": yoy}


# ── 스냅샷 읽기(+무결성 검사)
def snapshot_id_of(files: dict) -> str:
    """manifest 의 files{이름: {sha256}} → snapshot_id (build_snapshot.py 와 같은 규칙)."""
    joined = "\n".join(f"{n}:{files[n]['sha256']}" for n in sorted(files))
    return hashlib.sha256(joined.encode()).hexdigest()[:12]


def manifest_errors(d: Path) -> tuple[dict, list[str]]:
    """스냅샷 폴더의 manifest 와 실제 파일을 대조. (manifest, 문제 목록) — 문제 없으면 빈 목록."""
    mpath = d / "manifest.json"
    if not mpath.exists():
        raise FileNotFoundError(f"스냅샷 없음: {d} → python tasks.py snapshot --as-of {d.name} 실행 필요"
                                f" (scripts/fetch_snapshot.py → scripts/build_snapshot.py)")
    man = json.loads(mpath.read_text(encoding="utf-8"))
    errs = []
    for name, meta in man["files"].items():
        p = d / name
        if not p.exists():
            errs.append(f"{name}: 파일 없음")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != meta["sha256"]:
            errs.append(f"{name}: 스냅샷 파일이 manifest 와 다름(sha256 불일치)")
    if snapshot_id_of(man["files"]) != man["snapshot_id"]:
        errs.append("snapshot_id 가 파일 해시와 맞지 않음")
    return man, errs


def load_snapshot(as_of: date = AS_OF, root: Path = SNAPSHOT_ROOT) -> dict:
    """manifest.json 의 sha256 과 실제 파일을 대조한 뒤 DataFrame 으로 읽는다. 다르면 ValueError."""
    d = root / str(as_of)
    man, errs = manifest_errors(d)
    if errs:
        raise ValueError("; ".join(errs))
    return {"manifest": man,
            "domestic": pd.read_parquet(d / "domestic_753.parquet"),
            "foreign": pd.read_parquet(d / "foreign_748.parquet"),
            "weather": pd.read_parquet(d / "weather_asos.parquet")}


# ── 순수 함수: 일별 합계
def daily_total(df: pd.DataFrame, emd: str, value: str, key_cols: list[str]) -> pd.Series:
    """읍면동 하루 합계. 그날 행이 '전체 구성(성별×연령대, 또는 국적)'보다 모자라면 그날은 빼고 돌려준다."""
    expected = len(df[key_cols].drop_duplicates())      # 스냅샷 전체에서 관측된 구성 수(753:16, 748:10)
    sub = df[df["emd"] == emd]
    g = sub.groupby("base_date")[value].agg(["sum", "size"])
    return g.loc[g["size"] == expected, "sum"].astype(float)


def window_mean(daily: pd.Series, w: Window) -> tuple[float | None, float]:
    """창 평균과 결측 비율. 관측일이 하나도 없으면 (None, 1.0)."""
    days = w.days()
    have = daily.reindex(days).dropna()
    missing = 1 - len(have) / len(days)
    return (float(have.mean()) if len(have) else None), round(missing, 4)


def pct_change(recent: float | None, base: float | None) -> float | None:
    if recent is None or not base:
        return None
    return round((recent - base) / base * 100, 1)


def rain_days(weather: pd.DataFrame, stn: str, mm: float = RAIN_MM) -> set[date]:
    """일강수량 ≥ mm 인 날. sum_rn_mm 이 null(기상청 빈값=무강수)이면 0mm 로 본다."""
    w = weather[weather["stn_id"] == stn]
    return set(w.loc[w["sum_rn_mm"].fillna(0.0) >= mm, "base_date"])


def rain_effect_pp(daily: pd.Series, rainy: set[date], recent: Window, prev: Window) -> float:
    """직전 대비 변화율 중 '강수일 비중 차이'로 설명되는 크기(%p). 양수 = 강수가 감소 쪽으로 기여.

    gap   = (강수일 평균 − 비강수일 평균) / 비강수일 평균      (두 창 합쳐 계산)
    share = 창 안 강수일 비율
    effect = −gap × (share_recent − share_prev) × 100
    한계: 요일·휴가철 등 다른 요인과 섞일 수 있음(원인 단정 금지).
    """
    days = recent.days() + prev.days()
    s = daily.reindex(days).dropna()
    r = s[[d in rainy for d in s.index]]
    n = s[[d not in rainy for d in s.index]]
    if r.empty or n.empty or not n.mean():
        return 0.0
    gap = (r.mean() - n.mean()) / n.mean()

    def share(w: Window) -> float:
        obs = [d for d in w.days() if d in s.index]
        return sum(d in rainy for d in obs) / len(obs) if obs else 0.0

    return round(float(-gap * (share(recent) - share(prev)) * 100), 1)


# ── 읍면동 1곳 지표
def foreign_gap_ratio(fo: pd.DataFrame, emd: str, w: Window, n_nat: int | None = None) -> float:
    """748: (빠진 행 + visit_pop=0 행) / (일수 × 국적 수). 소수값 비공개 가능성 → 높으면 외국인 지표 신뢰 낮음.
    validate(검사 5)와 적합성 판정(F6)이 같은 이 함수를 쓴다."""
    n_nat = n_nat or fo["nationality"].nunique()
    days = set(w.days())
    part = fo[(fo["emd"] == emd) & fo["base_date"].isin(days)]
    expected = len(days) * n_nat
    gap = (expected - len(part)) + int((part["visit_pop"] == 0).sum())
    return round(gap / expected, 4) if expected else 1.0


def dong_metrics(snap: dict, emd: str, ws: dict[str, Window] | None = None) -> dict:
    ws = ws or windows()
    dom = daily_total(snap["domestic"], emd, "visit_pop", ["gender", "age_group"])
    fo = daily_total(snap["foreign"], emd, "visit_pop", ["nationality"])
    m_dom = {k: window_mean(dom, w) for k, w in ws.items()}
    m_fo = {k: window_mean(fo, w) for k, w in ws.items()}
    missing = {k: v[1] for k, v in m_dom.items()}
    fo_gap = max(foreign_gap_ratio(snap["foreign"], emd, w) for w in ws.values())
    stn = STATION_OF[emd]
    return {
        "dong_name": emd,
        "visit_prev_pct": pct_change(m_dom["recent"][0], m_dom["prev"][0]),
        "visit_yoy_pct": pct_change(m_dom["recent"][0], m_dom["yoy"][0]),
        "rain_effect_pp": rain_effect_pp(dom, rain_days(snap["weather"], stn), ws["recent"], ws["prev"]),
        "visit_for_prev_pct": pct_change(m_fo["recent"][0], m_fo["prev"][0]),
        "visit_for_yoy_pct": pct_change(m_fo["recent"][0], m_fo["yoy"][0]),
        "_quality": {"station": stn, "missing_ratio": missing,
                     "missing_ratio_foreign": {k: v[1] for k, v in m_fo.items()},
                     "foreign_gap_ratio": fo_gap,          # 세 창 중 최댓값 → F6 판정에 사용
                     "fit": max(missing.values()) <= MAX_MISSING_RATIO},
    }


# ── _region() 과 같은 모양으로 조립
def build_region(dong_codes: dict[str, str], as_of: date = AS_OF, root: Path = SNAPSHOT_ROOT) -> dict:
    """dong_codes: {읍면동 이름: 코드}. 부적합(결측 > 10%) 읍면동은 dongs 에서 빼고 excluded 에 사유를 남긴다."""
    snap = load_snapshot(as_of, root)
    ws = windows()
    files = snap["manifest"]["files"]
    dongs, excluded = {}, []
    for name, code in dong_codes.items():
        if name not in STATION_OF:
            continue
        m = dong_metrics(snap, name, ws)
        if m["_quality"]["fit"]:
            dongs[code] = m
        else:
            excluded.append(f"{name}: 비교 창 결측 {m['_quality']['missing_ratio']} > {MAX_MISSING_RATIO} → 부적합")
    f753, f748, fw = files["domestic_753.parquet"], files["foreign_748.parquet"], files["weather_asos.parquet"]
    freshness = [
        {"dataset_no": 753, "name": f753["name"], "last_obs_date": f753["last_date"], "unit": "읍면동/일", "used": True,
         "reason": f"진단 지표(내국인 방문). 실제 마지막 관측일 {f753['last_date']} → 분석 기준일로 사용"},
        {"dataset_no": 748, "name": f748["name"], "last_obs_date": f748["last_date"], "unit": "읍면동/일", "used": True,
         "reason": "보조 지표(외국인 단기체류 변화율). 753과 합산하지 않음"},
        {"dataset_no": 15059093, "name": fw["name"], "last_obs_date": fw["last_date"], "unit": "관측소/일", "used": True,
         "reason": f"강수일(≥{RAIN_MM:g}mm) 판정, 가장 가까운 관측소. 기준일에서 절단(조사일 API 최신 {ASOS_LATEST_AT_SURVEY})"},
        *({"dataset_no": no, **v} for no, v in SURVEY.items()),
    ]
    return {
        "snapshot_id": snap["manifest"]["snapshot_id"],
        "as_of": str(as_of),
        "window": {k: w.label() for k, w in ws.items()},
        "dongs": dongs,
        "freshness": freshness,
        "excluded": excluded,
    }
