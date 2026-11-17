"""★A 스냅샷 검증 게이트 테스트 — 작은 가짜 스냅샷을 일부러 망가뜨려 각 검사가 실제로 막는지 확인한다.
(실제 스냅샷·API 불필요. 창은 1주씩으로 줄여서 빠르게)
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import hashlib

import pandas as pd
import pytest

from coach.data import region_real as R
from coach.data import validate as V

AS_OF = date(2026, 3, 1)                       # 일요일
WS = R.windows(AS_OF, weeks=1)                 # 최근·직전·전년 각 7일 → 결측 허용 int(7×0.1)=0일
DONG, STN = "노형동", R.STATION_OF["노형동"]
DAYS = sorted({d for w in WS.values() for d in w.days()})


def _tables():
    dom = pd.DataFrame([(d, "제주시", DONG, g, a, 100, 10, 50) for d in DAYS for g in ("남", "여") for a in ("20대", "30대")],
                       columns=["base_date", "city", "emd", "gender", "age_group", "resd_pop", "work_pop", "visit_pop"])
    fo = pd.DataFrame([(d, "제주시", DONG, n, 30) for d in DAYS for n in ("일본", "중국")],
                      columns=["base_date", "city", "emd", "nationality", "visit_pop"])
    wt = pd.DataFrame([(d, STN, "제주", 0.0, 20.0, 15.0, 25.0) for d in DAYS],
                      columns=["base_date", "stn_id", "stn_name", "sum_rn_mm", "avg_ta_c", "min_ta_c", "max_ta_c"])
    for df in (dom, fo, wt):
        for c in df.columns:
            if df[c].dtype == object and c != "base_date":
                df[c] = df[c].astype("string")
    return {"domestic": dom, "foreign": fo, "weather": wt}


def _write(root, tables):
    d = root / str(AS_OF)
    d.mkdir(parents=True, exist_ok=True)
    files = {}
    for key, fname in V.TABLE.items():
        tables[key].to_parquet(d / fname, index=False)
        files[fname] = {"sha256": hashlib.sha256((d / fname).read_bytes()).hexdigest()}
    (d / "manifest.json").write_text(json.dumps({"snapshot_id": R.snapshot_id_of(files), "files": files}), encoding="utf-8")
    return d


def _run(root):
    checks = V.run_all(AS_OF, root, dongs=[DONG], ws=WS)
    return {c.no: c.status for c in checks}, V.exit_code(checks)


def test_clean_snapshot_passes(tmp_path):
    _write(tmp_path, _tables())
    status, code = _run(tmp_path)
    assert status == {1: "PASS", 2: "PASS", 3: "PASS", 4: "PASS", 5: "PASS", 6: "PASS"} and code == 0


def test_tampered_file_fails_hash(tmp_path):
    d = _write(tmp_path, _tables())
    t = _tables()
    t["foreign"].loc[0, "visit_pop"] = 999                       # 내용 바꿔치기 (manifest 는 그대로)
    t["foreign"].to_parquet(d / "foreign_748.parquet", index=False)
    status, code = _run(tmp_path)
    assert status[1] == "FAIL" and code == 1


def test_wrong_dtype_fails_schema(tmp_path):
    t = _tables()
    t["domestic"]["visit_pop"] = t["domestic"]["visit_pop"].astype(float)
    _write(tmp_path, t)
    status, code = _run(tmp_path)
    assert status[2] == "FAIL" and code == 1


def test_missing_day_in_window_fails(tmp_path):
    t = _tables()
    gone = WS["recent"].days()[3]
    t["domestic"] = t["domestic"][t["domestic"]["base_date"] != gone]   # 하루가 통째로 빠짐
    _write(tmp_path, t)
    status, code = _run(tmp_path)
    assert status[3] == "FAIL" and code == 1


def test_incomplete_day_counts_as_missing(tmp_path):
    t = _tables()
    d0 = WS["prev"].days()[0]
    t["domestic"] = t["domestic"].drop(t["domestic"].index[t["domestic"]["base_date"] == d0][:1])  # 그날 1행만 빠짐
    _write(tmp_path, t)
    assert _run(tmp_path)[0][3] == "FAIL"


def test_unknown_dong_fails_coverage(tmp_path):
    t = _tables()
    t["foreign"]["emd"] = "다른동"
    _write(tmp_path, t)
    status, code = _run(tmp_path)
    assert status[4] == "FAIL" and code == 1


def test_many_foreign_zeros_warns_but_passes(tmp_path):
    t = _tables()
    t["foreign"].loc[t["foreign"]["nationality"] == "일본", "visit_pop"] = 0   # 50% 가 0
    _write(tmp_path, t)
    status, code = _run(tmp_path)
    assert status[5] == "WARN" and code == 0        # 경고는 멈추지 않음(S8 에서 '내국인 기준만'으로 축소)


def test_old_last_obs_warns(tmp_path):
    _write(tmp_path, _tables())
    late = AS_OF + timedelta(days=R.LAG_WARN_DAYS + 1)          # 데이터는 AS_OF 까지인데 기준일을 8일 뒤로 잡음
    (tmp_path / str(AS_OF)).rename(tmp_path / str(late))
    checks = V.run_all(late, tmp_path, dongs=[DONG], ws=WS)
    st = {c.no: c.status for c in checks}
    assert st[6] == "WARN" and V.exit_code(checks) == 0


def test_missing_snapshot_raises_with_how_to(tmp_path):
    with pytest.raises(FileNotFoundError, match="tasks.py snapshot"):
        V.run_all(AS_OF, tmp_path, dongs=[DONG], ws=WS)
