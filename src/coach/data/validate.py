"""★A 스냅샷 검증 게이트 — 분석에 쓰기 전에 데이터가 '쓸 만한지' 자동으로 확인한다.

검사 6개(CLAUDE.md §3 S7, 허용치는 노션 decisions 2026-11-17 ①):
  1. manifest sha256 ↔ 실제 파일                    → 다르면 FAIL
  2. 필수 컬럼·자료형(노션 data_spec 기준)             → 다르면 FAIL
  3. 비교 창 안 날짜 연속성(읍면동·관측소별 결측일)       → 허용(창의 10%)을 넘으면 FAIL
  4. 시범 읍면동·관측소가 스냅샷에 모두 있는지          → 없으면 FAIL
  5. 748 외국인 빠진 행 + 0값 비율                    → 허용(10%)을 넘으면 WARN
  6. 데이터별 last_obs_date ↔ AS_OF 지연             → 허용(7일)을 넘으면 WARN
결과는 PASS / WARN / FAIL. FAIL 이 하나라도 있으면 그 스냅샷으로 분석하면 안 된다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd
from pandas.api import types as pdt

from . import region_real as R

# 필수 컬럼과 자료형 (build_snapshot.py 가 만드는 모양 = data_spec)
SCHEMA = {
    "domestic_753.parquet": {"base_date": "date", "city": "str", "emd": "str", "gender": "str", "age_group": "str",
                             "resd_pop": "int", "work_pop": "int", "visit_pop": "int"},
    "foreign_748.parquet": {"base_date": "date", "city": "str", "emd": "str", "nationality": "str", "visit_pop": "int"},
    "weather_asos.parquet": {"base_date": "date", "stn_id": "str", "stn_name": "str", "sum_rn_mm": "float",
                             "avg_ta_c": "float", "min_ta_c": "float", "max_ta_c": "float"},
}
TABLE = {"domestic": "domestic_753.parquet", "foreign": "foreign_748.parquet", "weather": "weather_asos.parquet"}


@dataclass
class Check:
    no: int
    name: str
    status: str          # PASS | WARN | FAIL
    detail: str


def _type_ok(s: pd.Series, kind: str) -> bool:
    if kind == "int":
        return pdt.is_integer_dtype(s)
    if kind == "float":
        return pdt.is_float_dtype(s)
    if kind == "str":
        return pdt.is_string_dtype(s) and s.dropna().map(lambda v: isinstance(v, str)).all()
    if kind == "date":
        return s.dropna().map(lambda v: isinstance(v, date)).all()
    raise ValueError(kind)


def allowed_missing_days(w: R.Window, ratio: float = R.MAX_MISSING_RATIO) -> int:
    return int(len(w.days()) * ratio)          # 42일 × 10% = 4일


# ── 개별 검사 (순수 함수: 표를 받아 Check 를 돌려준다)
def check_schema(tables: dict[str, pd.DataFrame]) -> Check:
    errs = []
    for key, fname in TABLE.items():
        df = tables[key]
        for col, kind in SCHEMA[fname].items():
            if col not in df.columns:
                errs.append(f"{fname}: '{col}' 없음")
            elif not _type_ok(df[col], kind):
                errs.append(f"{fname}: '{col}' 자료형이 {kind} 아님({df[col].dtype})")
    return Check(2, "필수 컬럼·자료형", "FAIL" if errs else "PASS", "; ".join(errs) or "3개 표 모두 일치")


def check_continuity(tables: dict, ws: dict[str, R.Window], dongs: list[str], stations: list[str]) -> Check:
    """읍면동(753 완전한 날)·관측소(ASOS 평균기온 있는 날)별로 창 안 결측일 수."""
    worst, bad = [], []
    for emd in dongs:
        s = R.daily_total(tables["domestic"], emd, "visit_pop", ["gender", "age_group"])
        for k, w in ws.items():
            miss = len([d for d in w.days() if d not in s.index])
            worst.append(miss)
            if miss > allowed_missing_days(w):
                bad.append(f"753 {emd} {k} 결측 {miss}일")
    wt = tables["weather"]
    for stn in stations:
        have = set(wt.loc[(wt["stn_id"] == stn) & wt["avg_ta_c"].notna(), "base_date"])
        for k, w in ws.items():
            miss = len([d for d in w.days() if d not in have])
            worst.append(miss)
            if miss > allowed_missing_days(w):
                bad.append(f"ASOS {stn} {k} 결측 {miss}일")
    lim = allowed_missing_days(next(iter(ws.values())))
    detail = "; ".join(bad) if bad else f"최대 결측 {max(worst, default=0)}일 (허용 {lim}일)"
    return Check(3, "비교 창 날짜 연속성", "FAIL" if bad else "PASS", detail)


def check_coverage(tables: dict, dongs: list[str], stations: list[str]) -> Check:
    miss = []
    for key in ("domestic", "foreign"):
        have = set(tables[key]["emd"].unique())
        miss += [f"{TABLE[key]}에 {d} 없음" for d in dongs if d not in have]
    miss += [f"{d}: 관측소 매핑 없음" for d in dongs if d not in R.STATION_OF]
    have_stn = set(tables["weather"]["stn_id"].unique())
    miss += [f"ASOS 관측소 {s} 없음" for s in stations if s not in have_stn]
    return Check(4, "시범 읍면동·관측소 존재", "FAIL" if miss else "PASS",
                 "; ".join(miss) or f"{len(dongs)}개 읍면동 · 관측소 {stations}")


def check_foreign_gaps(fo: pd.DataFrame, ws: dict[str, R.Window], dongs: list[str],
                       max_ratio: float = R.FOREIGN_GAP_MAX_RATIO) -> Check:
    """748: (빠진 행 + visit_pop=0 행) / (일수 × 국적 수). 소수값 비공개 가능성 → 높으면 외국인 지표 신뢰 낮음."""
    n_nat = fo["nationality"].nunique()
    worst, bad = 0.0, []
    for emd in dongs:
        for k, w in ws.items():
            ratio = R.foreign_gap_ratio(fo, emd, w, n_nat)     # 적합성 판정(F6)과 같은 함수
            worst = max(worst, ratio)
            if ratio > max_ratio:
                bad.append(f"{emd} {k} {ratio:.1%}")
    detail = ("허용 초과: " + "; ".join(bad)) if bad else f"최대 {worst:.2%} (허용 {max_ratio:.0%})"
    return Check(5, "748 외국인 빠진 행·0값 비율", "WARN" if bad else "PASS", detail)


def check_lag(tables: dict, as_of: date, warn_days: int = R.LAG_WARN_DAYS) -> Check:
    parts, warn = [], []
    for key, fname in TABLE.items():
        last = max(tables[key]["base_date"])
        lag = (as_of - last).days
        parts.append(f"{fname.split('.')[0]} 마지막 {last} (AS_OF−{lag}일)")
        if lag > warn_days:
            warn.append(f"{fname}: AS_OF 보다 {lag}일 이전에서 끝남")
    return Check(6, "last_obs_date ↔ AS_OF 지연", "WARN" if warn else "PASS", "; ".join(warn or parts))


# ── 전체 실행
def run_all(as_of: date = R.AS_OF, root: Path = R.SNAPSHOT_ROOT, dongs: list[str] | None = None,
            ws: dict[str, R.Window] | None = None) -> list[Check]:
    d = root / str(as_of)
    dongs = dongs or list(R.STATION_OF)
    stations = sorted({R.STATION_OF[x] for x in dongs if x in R.STATION_OF})
    ws = ws or R.windows(R.last_sunday(as_of))
    man, errs = R.manifest_errors(d)
    out = [Check(1, "manifest sha256", "FAIL" if errs else "PASS",
                 "; ".join(errs) or f"{len(man['files'])}개 파일 일치 · snapshot_id {man['snapshot_id']}")]
    try:
        tables = {k: pd.read_parquet(d / f) for k, f in TABLE.items()}
    except Exception as e:  # 파일이 깨졌거나 없으면 나머지 검사는 할 수 없음
        return out + [Check(2, "필수 컬럼·자료형", "FAIL", f"표를 읽을 수 없음: {type(e).__name__}: {e}")]
    out.append(check_schema(tables))
    if out[-1].status == "FAIL":
        return out
    out += [check_continuity(tables, ws, dongs, stations), check_coverage(tables, dongs, stations),
            check_foreign_gaps(tables["foreign"], ws, dongs), check_lag(tables, as_of)]
    return out


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status == "FAIL" for c in checks) else 0


def to_json(checks: list[Check], as_of: date) -> str:
    return json.dumps({"as_of": str(as_of), "passed": exit_code(checks) == 0,
                       "checks": [asdict(c) for c in checks]}, ensure_ascii=False, indent=2)
