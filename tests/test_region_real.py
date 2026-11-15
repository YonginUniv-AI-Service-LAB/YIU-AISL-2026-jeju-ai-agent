"""★A region_real 순수 함수 단위 테스트 — 스냅샷·API 없이 작은 표로 규칙만 확인한다."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pandas as pd
import pytest

from coach.data import region_real as R

D = date(2026, 1, 5)  # 월요일


def test_windows_same_weekdays_and_lengths():
    ws = R.windows(recent_end=date(2026, 8, 30))
    assert ws["recent"].label() == "2026-07-20~2026-08-30"
    assert ws["prev"].label() == "2026-06-08~2026-07-19"
    assert ws["yoy"].label() == "2025-07-21~2025-08-31"
    for w in ws.values():
        assert len(w.days()) == 42 and w.start.weekday() == ws["recent"].start.weekday()


def _dom(rows):
    return pd.DataFrame(rows, columns=["base_date", "emd", "gender", "age_group", "visit_pop"])


def test_daily_total_drops_incomplete_day():
    full = [(D, "A", g, a, 10) for g in "MF" for a in ("x", "y")]            # 구성 4개
    part = [(D + timedelta(1), "A", "M", "x", 10)]                           # 하루는 1개만 → 제외
    s = R.daily_total(_dom(full + part), "A", "visit_pop", ["gender", "age_group"])
    assert list(s.index) == [D] and s[D] == 40


def test_window_mean_reports_missing_without_zero_fill():
    w = R.Window(D, D + timedelta(3))
    s = pd.Series({D: 10.0, D + timedelta(1): 30.0})
    mean, missing = R.window_mean(s, w)
    assert mean == 20.0 and missing == 0.5     # 0으로 채웠다면 평균 10


def test_pct_change_none_safe():
    assert R.pct_change(110, 100) == 10.0
    assert R.pct_change(None, 100) is None and R.pct_change(10, 0) is None


def test_rain_days_null_is_no_rain():
    w = pd.DataFrame({"base_date": [D, D + timedelta(1), D + timedelta(2)], "stn_id": ["184"] * 3,
                      "sum_rn_mm": [None, 0.5, 1.0]})
    assert R.rain_days(w, "184") == {D + timedelta(2)}


def test_rain_effect_positive_when_more_rain_recently_and_rain_lowers_visits():
    prev, recent = R.Window(D, D + timedelta(3)), R.Window(D + timedelta(4), D + timedelta(7))
    days = prev.days() + recent.days()
    rainy = {days[0], days[4], days[5], days[6]}           # 직전 1/4, 최근 3/4 강수일
    s = pd.Series({d: (50.0 if d in rainy else 100.0) for d in days})
    assert R.rain_effect_pp(s, rainy, recent, prev) == pytest.approx(25.0)   # −(−0.5)×(0.75−0.25)×100
    assert R.rain_effect_pp(s, set(), recent, prev) == 0.0                     # 강수일 없으면 0


def _fake_snapshot(root):
    """작은 가짜 스냅샷(+manifest) 만들기 — build_snapshot.py 와 같은 해시 규칙."""
    import hashlib, json
    d = root / str(R.AS_OF)
    d.mkdir(parents=True)
    files = {}
    for name in ("domestic_753.parquet", "foreign_748.parquet", "weather_asos.parquet"):
        pd.DataFrame({"base_date": [D]}).to_parquet(d / name, index=False)
        files[name] = {"sha256": hashlib.sha256((d / name).read_bytes()).hexdigest()}
    joined = "\n".join(f"{n}:{files[n]['sha256']}" for n in sorted(files))
    man = {"snapshot_id": hashlib.sha256(joined.encode()).hexdigest()[:12], "files": files}
    (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return d


def test_load_snapshot_accepts_intact_and_rejects_tampered(tmp_path):
    d = _fake_snapshot(tmp_path)
    assert R.load_snapshot(R.AS_OF, tmp_path)["manifest"]["snapshot_id"]
    pd.DataFrame({"base_date": [D, D]}).to_parquet(d / "foreign_748.parquet", index=False)  # 내용 바꿔치기
    with pytest.raises(ValueError, match="manifest"):
        R.load_snapshot(R.AS_OF, tmp_path)


def test_load_snapshot_missing_tells_how_to_build(tmp_path):
    with pytest.raises(FileNotFoundError, match="build_snapshot"):
        R.load_snapshot(R.AS_OF, tmp_path)
