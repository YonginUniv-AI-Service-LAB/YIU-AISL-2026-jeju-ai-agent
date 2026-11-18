"""S2-2: data/raw/fetch/<as_of>/ 의 API 원본(JSON) → data/snapshot/<as_of>/ parquet + manifest.json

원칙
- 스냅샷에는 원본 값을 그대로(타입만 정리) 담는다. 결측 처리·합계 등 해석 규칙은 S3 계산 코드에 둔다.
- 파일별 sha256 → manifest.json. snapshot_id = (파일명:해시 목록)의 sha256 앞 12자리.
- 같은 원본이면 같은 파일·같은 snapshot_id 가 나와야 한다(행 정렬 고정).

사용: python tasks.py snapshot --as-of 2026-08-31   (fetch 다음에 자동 실행)
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from coach.data.region_real import AS_OF as _AS_OF  # noqa: E402  (기준일은 region_real 한 곳에서)

AS_OF = str(_AS_OF)
SRC = ROOT / "data" / "raw" / "fetch" / AS_OF.replace("-", "")
OUT = ROOT / "data" / "snapshot" / AS_OF


def _pages(sub: str) -> list[dict]:
    rows = []
    for p in sorted((SRC / sub).rglob("p*.json")):
        rows += json.loads(p.read_text(encoding="utf-8"))["response"]["data"] or []
    return rows


def domestic() -> pd.DataFrame:
    """No.753 내국인 유동인구: 읍면동 × 일 × 성별 × 연령대."""
    df = pd.DataFrame(_pages("753"))
    df["base_date"] = pd.to_datetime(df.pop("baseDate"), format="%Y%m%d").dt.date
    df = df.rename(columns={"ageGroup": "age_group", "resdPop": "resd_pop", "workPop": "work_pop",
                            "visitPop": "visit_pop"})
    cols = ["base_date", "city", "emd", "gender", "age_group", "resd_pop", "work_pop", "visit_pop"]
    return df[cols].sort_values(cols[:5]).reset_index(drop=True)


def foreign() -> pd.DataFrame:
    """No.748 외국인 단기체류 유동인구: 읍면동 × 일 × 국적. 빠진 행은 채우지 않는다."""
    df = pd.DataFrame(_pages("748"))
    df["base_date"] = pd.to_datetime(df.pop("baseDate"), format="%Y%m%d").dt.date
    df = df.rename(columns={"visitPop": "visit_pop"})
    cols = ["base_date", "city", "emd", "nationality", "visit_pop"]
    return df[cols].sort_values(cols[:4]).reset_index(drop=True)


def weather() -> pd.DataFrame:
    """기상청 ASOS 일자료(필요 컬럼만). sum_rn 빈 문자열은 null 그대로 둔다(0 해석은 S3 규칙)."""
    rows = []
    for p in sorted((SRC / "asos").glob("*.json")):
        body = json.loads(p.read_text(encoding="utf-8"))["response"]["response"]["body"]
        items = body["items"]["item"]
        rows += items if isinstance(items, list) else [items]
    df = pd.DataFrame(rows)
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
    return out.sort_values(["base_date", "stn_id"]).reset_index(drop=True)


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tables = {
        "domestic_753.parquet": (domestic(), {"dataset_no": 753, "name": "읍면동 단위 내국인 유동인구",
                                              "source": "제주데이터허브", "unit": "읍면동/일/성별/연령대"}),
        "foreign_748.parquet": (foreign(), {"dataset_no": 748, "name": "읍면동 외국인 단기체류 유동인구",
                                            "source": "제주데이터허브", "unit": "읍면동/일/국적"}),
        "weather_asos.parquet": (weather(), {"dataset_no": 15059093, "name": "기상청 지상(종관, ASOS) 일자료",
                                             "source": "공공데이터포털(기상청)", "unit": "관측소/일"}),
    }
    files = {}
    for name, (df, meta) in tables.items():
        p = OUT / name
        df.to_parquet(p, index=False, engine="pyarrow", compression="zstd")
        files[name] = {**meta, "sha256": sha256(p), "rows": len(df),
                       "first_date": str(min(df["base_date"])), "last_date": str(max(df["base_date"])),
                       "emd_or_stn": sorted(df["emd"].unique()) if "emd" in df else sorted(df["stn_id"].unique())}
    joined = "\n".join(f"{n}:{files[n]['sha256']}" for n in sorted(files))
    snapshot_id = hashlib.sha256(joined.encode()).hexdigest()[:12]
    manifest = {"snapshot_id": snapshot_id, "as_of": AS_OF, "built_on": str(date.today()),
                "note": "원본 값 그대로(타입만 정리). 해석 규칙은 src/coach/data/region_real.py",
                "files": files}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for n, f in files.items():
        print(f"{n}: {f['rows']}행 {f['first_date']}~{f['last_date']} sha256={f['sha256'][:12]}…")
    print("snapshot_id =", snapshot_id)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
