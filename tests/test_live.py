"""★A 라이브 모드 테스트 — 네트워크 없이, 가짜 fetcher 가 기존 스냅샷 데이터를 'API 응답 모양'으로 돌려준다.
핵심: 같은 기간이면 라이브 결과 = 스냅샷 모드 결과(같은 계산 함수). 스냅샷이 없으면 건너뛴다."""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pytest

pd = pytest.importorskip("pandas")
from coach.config import MOCK_DIR  # noqa: E402
from coach.data import fitness as F  # noqa: E402
from coach.data import region_live as L  # noqa: E402
from coach.data import region_real as R  # noqa: E402

CODES = {k: v for k, v in json.loads((MOCK_DIR / "dong_codes.json").read_text(encoding="utf-8")).items()
         if not k.startswith("_")}
NOHYEONG = CODES["노형동"]
TODAY = date(2026, 11, 17)

try:
    SNAP = R.load_snapshot()
except FileNotFoundError:
    SNAP = None
pytestmark = pytest.mark.skipif(SNAP is None, reason="스냅샷 없음(python tasks.py snapshot)")


class SnapshotFetcher:
    """스냅샷 표를 API 응답(camelCase, YYYYMMDD) 모양으로 잘라 돌려주는 가짜. 마지막 관측일 = 스냅샷 마지막 날."""
    def __init__(self, last=date(2026, 8, 31)):
        self.last, self.calls = last, []

    def latest(self, no, emd, upper):
        self.calls.append(("latest", no, emd, upper))
        return min(upper, self.last)

    def hub(self, no, emd, start, end):
        self.calls.append(("hub", no, emd, start, end))
        df = SNAP["domestic" if no == 753 else "foreign"]
        df = df[(df["emd"] == emd) & (df["base_date"] >= start) & (df["base_date"] <= end)]
        ren = ({"age_group": "ageGroup", "resd_pop": "resdPop", "work_pop": "workPop", "visit_pop": "visitPop"}
               if no == 753 else {"visit_pop": "visitPop"})
        out = df.rename(columns=ren).assign(baseDate=df["base_date"].map(lambda d: d.strftime("%Y%m%d")))
        return out.drop(columns=["base_date"]).to_dict("records")

    def asos(self, stn, start, end):
        w = SNAP["weather"]
        w = w[(w["stn_id"] == stn) & (w["base_date"] >= start) & (w["base_date"] <= end)]
        f = lambda v: "" if pd.isna(v) else f"{v}"  # noqa: E731  (기상청은 빈 강수량을 "" 로 줌)
        return [{"tm": str(r.base_date), "stnId": r.stn_id, "stnNm": r.stn_name, "sumRn": f(r.sum_rn_mm),
                 "avgTa": f(r.avg_ta_c), "minTa": f(r.min_ta_c), "maxTa": f(r.max_ta_c)} for r in w.itertuples()]


class BrokenFetcher(SnapshotFetcher):
    def latest(self, *a):
        raise RuntimeError("753 호출 실패: HTTPError: 502 Bad Gateway | body=...")


def store(last_monday, n=12):
    return {"dong_code": NOHYEONG, "dong_name": "노형동",
            "weeks": [{"week_start": str(last_monday - timedelta(weeks=n - 1 - i)), "orders": 300, "open_days": 6,
                       "sales_krw": 0} for i in range(n)]}


def test_target_date_is_last_week_sunday_or_yesterday():
    assert L.target_date(store(date(2026, 8, 24))) == date(2026, 8, 30)
    assert L.target_date({"weeks": []}, today=TODAY) == date(2026, 11, 16)


def test_live_equals_snapshot_mode_for_same_period(tmp_path):
    """같은 기간 → 라이브 지표 = 스냅샷 모드 지표 (같은 순수 함수 dong_metrics)."""
    live = L.build_region_live(CODES, NOHYEONG, date(2026, 8, 30), SnapshotFetcher(), TODAY, tmp_path)
    snap = R.build_region(CODES)
    assert live["window"] == snap["window"]
    keys = ["visit_prev_pct", "visit_yoy_pct", "rain_effect_pp", "visit_for_prev_pct", "visit_for_yoy_pct"]
    assert {k: live["dongs"][NOHYEONG][k] for k in keys} == {k: snap["dongs"][NOHYEONG][k] for k in keys}


def test_live_only_fetches_needed_dong_and_periods(tmp_path):
    fx = SnapshotFetcher()
    L.build_region_live(CODES, NOHYEONG, date(2026, 8, 30), fx, TODAY, tmp_path)
    hubs = [c for c in fx.calls if c[0] == "hub"]
    assert {c[2] for c in hubs} == {"노형동"}                                   # 그 읍면동만
    assert {(c[3], c[4]) for c in hubs} == {(date(2026, 6, 8), date(2026, 8, 30)), (date(2025, 7, 21), date(2025, 8, 31))}


def test_late_store_period_caps_window_and_fitness_explains(tmp_path):
    """11월 주문(기준일 11-08) → 원천은 8/31까지 → 창은 7/20~8/30, F5(지연)·F7(기간 불일치)로 안내."""
    si = store(date(2026, 11, 2))
    reg = L.build_region_live(CODES, NOHYEONG, L.target_date(si), SnapshotFetcher(), TODAY, tmp_path)
    assert reg["window"]["recent"] == "2026-07-20~2026-08-30" and reg["as_of"] == "2026-11-08"
    f = F.judge(si, "노형동 카페", reg, date.fromisoformat(reg["as_of"]))
    assert set(f.cases) == {"F5", "F7"} and f.mode == "region_only"


def test_earlier_period_moves_windows_with_store(tmp_path):
    """주문 기록이 6월에 끝나면 동네 창도 6월로 옮겨 조회한다(이번엔 스냅샷에 전년 동기가 없어 → 부적합 처리)."""
    fx = SnapshotFetcher()
    reg = L.build_region_live(CODES, NOHYEONG, date(2026, 6, 28), fx, TODAY, tmp_path)
    assert reg["window"]["recent"] == "2026-05-18~2026-06-28"
    assert reg["dongs"] == {} and "빠진 날" in reg["excluded"][0]     # 가짜 fetcher 엔 2025-05 데이터가 없음


def test_outside_pilot_returns_empty_region_without_fetching(tmp_path):
    fx = SnapshotFetcher()
    reg = L.build_region_live(CODES, "5011099999", date(2026, 8, 30), fx, TODAY, tmp_path)
    assert reg["dongs"] == {} and fx.calls == []


def test_fetch_failure_falls_back_to_snapshot_with_reason(tmp_path):
    reg = L.region_for(store(date(2026, 8, 24)), CODES, lambda: R.build_region(CODES), BrokenFetcher(), TODAY, tmp_path)
    assert reg["snapshot_id"] == SNAP["manifest"]["snapshot_id"] and reg["live"]["fallback"]
    assert "실시간 동네 데이터 조회 실패" in reg["excluded"][-1] and "502" in reg["excluded"][-1]


def test_live_data_is_frozen_as_verifiable_snapshot(tmp_path):
    reg = L.build_region_live(CODES, NOHYEONG, date(2026, 8, 30), SnapshotFetcher(), TODAY, tmp_path)
    d = tmp_path / "노형동_2026-08-30"
    man, errs = R.manifest_errors(d)
    assert errs == [] and man["snapshot_id"] == reg["snapshot_id"] and man["mode"] == "live"


def test_graph_end_to_end_in_live_mode(tmp_path, monkeypatch):
    """B → A(라이브 조회) → C(실험·검증)까지. C 의 숫자 재검증도 같은 라이브 조회 결과를 본다(캐시)."""
    from coach.graph import run
    from coach.nodes import a_data as A
    monkeypatch.setattr(A, "USE_MOCK", False)
    monkeypatch.setattr(A, "USE_LIVE", True)
    monkeypatch.setattr(L, "ApiFetcher", lambda today=None: SnapshotFetcher())
    monkeypatch.setattr(L, "LIVE_DIR", tmp_path)
    A._live_region.cache_clear()
    si = {**store(date(2026, 8, 24)), "budget_krw": 200000, "staff_am": 1, "staff_pm": 1}   # 되묻기 없이 끝까지
    out = run("노형동에서 카페 해요. 손님이 줄었어요", si)
    A._live_region.cache_clear()
    ar = out["analysis_result"]
    fit = next(t for t in out["trace"] if t["node"] == "A.check_fitness")
    assert fit["region_source"] == "live" and fit["cases"] == ["F1"]
    assert ar["snapshot_id"] == fit["snapshot_id"] and out["verify_result"]["passed"]
    assert ar["metrics"]["region_visit_prev_pct"] == 7.3 and ar["diagnosis"] in {"only_me", "undetermined"}
