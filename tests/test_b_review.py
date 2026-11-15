"""B 재방문 점검(review_experiment) 테스트 — PR #3 리뷰 반영."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from coach.nodes import b_orchestrator as B


def _weeks(orders_days):
    """[(주문, 영업일), ...] → 2026-09-07 부터 매주 월요일."""
    from datetime import date, timedelta
    d0 = date(2026, 9, 7)
    return [{"week_start": str(d0 + timedelta(weeks=i)), "sales_krw": 0, "orders": o, "open_days": d}
            for i, (o, d) in enumerate(orders_days)]


def _review(weeks, started_at, analysis=None):
    st = {"store_input": {"weeks": weeks}, "trace": [],
          "previous_experiment": {"catalog_id": "X01", "started_at": started_at}}
    if analysis:
        st["analysis_result"] = analysis
    return B.review_experiment(st)["experiment_review"]


def test_no_weeks_after_start_is_unknown():
    # 실험 전 주들끼리 크게 변해도(100 → 50) 시작 후 실적이 없으면 판정하지 않음
    w = _weeks([(100, 6), (100, 6), (50, 6), (50, 6)])
    rv = _review(w, "2026-12-01")
    assert rv["verdict"] == "확인 불가" and "시작 후 실적" in rv["reason"]


def test_splits_by_started_at():
    # 09-07, 09-14 = 실험 전 / 09-21, 09-28 = 실험 후 → 주문 +20%
    w = _weeks([(100, 6), (100, 6), (120, 6), (120, 6)])
    rv = _review(w, "2026-09-21")
    assert rv["weeks_compared"] == 2 and rv["orders_per_day_change_pct"] == 20.0
    assert rv["verdict"] == "개선 신호"


def test_open_days_normalized():
    # 주문 합계는 같지만 실험 후 영업일이 하루 적음 → 영업일당 주문은 늘어남
    w = _weeks([(120, 6), (120, 5)])
    rv = _review(w, "2026-09-14")
    assert rv["orders_per_day_change_pct"] == 20.0


def test_not_enough_before_weeks():
    w = _weeks([(100, 6), (120, 6), (120, 6)])   # 실험 전 1주 vs 후 2주
    rv = _review(w, "2026-09-14")
    assert rv["verdict"] == "확인 불가" and "실험 전" in rv["reason"]


def test_missing_started_at():
    st = {"store_input": {"weeks": _weeks([(100, 6)])}, "trace": [], "previous_experiment": {"catalog_id": "X01"}}
    assert B.review_experiment(st)["experiment_review"]["verdict"] == "확인 불가"


def test_region_note_reads_freshness():
    a = {"data_freshness": [{"dataset_no": 753, "last_obs_date": "2026-08-31"}]}
    rv = _review(_weeks([(100, 6), (100, 6)]), "2026-09-14", a)
    assert "2026-08-31" in rv["region_note"]