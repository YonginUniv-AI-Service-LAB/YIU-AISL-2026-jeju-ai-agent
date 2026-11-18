"""★A API 응답 → 표 변환, 표 → 스냅샷 폴더(parquet + manifest) 저장. 스냅샷 수집과 라이브 모드가 같이 쓴다.

원칙: 스냅샷에는 원본 값을 그대로(타입·컬럼명만 정리) 담는다. 결측 처리 등 해석 규칙은 region_real 에 둔다.
같은 원본이면 같은 파일·같은 snapshot_id 가 나와야 한다(행 정렬 고정).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd

from .region_real import snapshot_id_of

META = {
    "domestic_753.parquet": {"dataset_no": 753, "name": "읍면동 단위 내국인 유동인구", "source": "제주데이터허브",
                             "unit": "읍면동/일/성별/연령대"},
    "foreign_748.parquet": {"dataset_no": 748, "name": "읍면동 외국인 단기체류 유동인구", "source": "제주데이터허브",
                            "unit": "읍면동/일/국적"},
    "weather_asos.parquet": {"dataset_no": 15059093, "name": "기상청 지상(종관, ASOS) 일자료",
                             "source": "공공데이터포털(기상청)", "unit": "관측소/일"},
}


def domestic_df(rows: list[dict]) -> pd.DataFrame:
    """No.753 내국인 유동인구: 읍면동 × 일 × 성별 × 연령대."""
    cols = ["base_date", "city", "emd", "gender", "age_group", "resd_pop", "work_pop", "visit_pop"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    df["base_date"] = pd.to_datetime(df.pop("baseDate"), format="%Y%m%d").dt.date
    df = df.rename(columns={"ageGroup": "age_group", "resdPop": "resd_pop", "workPop": "work_pop",
                            "visitPop": "visit_pop"})
    return df[cols].drop_duplicates(cols[:5]).sort_values(cols[:5]).reset_index(drop=True)


def foreign_df(rows: list[dict]) -> pd.DataFrame:
    """No.748 외국인 단기체류 유동인구: 읍면동 × 일 × 국적. 빠진 행은 채우지 않는다."""
    cols = ["base_date", "city", "emd", "nationality", "visit_pop"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    df["base_date"] = pd.to_datetime(df.pop("baseDate"), format="%Y%m%d").dt.date
    df = df.rename(columns={"visitPop": "visit_pop"})
    return df[cols].drop_duplicates(cols[:4]).sort_values(cols[:4]).reset_index(drop=True)


def weather_df(items: list[dict]) -> pd.DataFrame:
    """기상청 ASOS 일자료(필요 컬럼만). sum_rn 빈 문자열은 null 그대로 둔다(0 해석은 region_real 규칙)."""
    df = pd.DataFrame(items)
    num = lambda s: pd.to_numeric(s.replace("", None), errors="coerce")  # noqa: E731
    out = pd.DataFrame({
        "base_date": pd.to_datetime(df["tm"]).dt.date,
        "stn_id": df["stnId"].astype(str),
        "stn_name": df["stnNm"],
        "sum_rn_mm": num(df["sumRn"]),
        "avg_ta_c": num(df["avgTa"]),
        "min_ta_c": num(df["minTa"]),
        "max_ta_c": num(df["maxTa"]),
    })
    return out.drop_duplicates(["base_date", "stn_id"]).sort_values(["base_date", "stn_id"]).reset_index(drop=True)


def write_snapshot(out: Path, tables: dict[str, pd.DataFrame], as_of: str, extra: dict | None = None) -> dict:
    """tables{domestic, foreign, weather} → out/*.parquet + manifest.json. manifest(dict) 반환."""
    out.mkdir(parents=True, exist_ok=True)
    names = {"domestic": "domestic_753.parquet", "foreign": "foreign_748.parquet", "weather": "weather_asos.parquet"}
    files = {}
    for key, name in names.items():
        df, p = tables[key], out / name
        df.to_parquet(p, index=False, engine="pyarrow", compression="zstd")
        files[name] = {**META[name], "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "rows": len(df),
                       "first_date": str(min(df["base_date"])) if len(df) else None,
                       "last_date": str(max(df["base_date"])) if len(df) else None,
                       "emd_or_stn": sorted(df["emd"].unique()) if "emd" in df else sorted(df["stn_id"].unique())}
    manifest = {"snapshot_id": snapshot_id_of(files), "as_of": as_of, "built_on": str(date.today()),
                "note": "원본 값 그대로(타입만 정리). 해석 규칙은 src/coach/data/region_real.py", **(extra or {}),
                "files": files}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
