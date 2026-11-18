"""★A 적합성 판정(F1~F7) 테스트 — 순수 함수 fitness.judge 를 작은 지역 데이터로 확인하고,
그래프 전체에서 판정이 진단·근거·사유에 반영되는지 mock 모드로 확인한다."""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pytest

from coach.config import MOCK_DIR, USE_MOCK
from coach.data import fitness as F

CODE = "5011066000"
REGION = {   # 실데이터 모드와 같은 모양(창에 날짜가 있음)
    "snapshot_id": "test00000000",
    "window": {"recent": "2026-07-20~2026-08-30", "prev": "2026-06-08~2026-07-19", "yoy": "2025-07-21~2025-08-31"},
    "dongs": {CODE: {"dong_name": "노형동", "visit_prev_pct": 7.3, "visit_yoy_pct": 0.2, "rain_effect_pp": -0.1,
                     "_quality": {"foreign_gap_ratio": 0.0}}},
    "freshness": [{"dataset_no": 753, "name": "내국인", "last_obs_date": "2026-08-31", "unit": "읍면동/일",
                   "used": True, "reason": "-"}],
}
AS_OF = date(2026, 8, 31)


def weeks(n, last_monday=date(2026, 8, 24)):
    return [{"week_start": str(last_monday - timedelta(weeks=n - 1 - i)), "orders": 300, "open_days": 6,
             "sales_krw": 0} for i in range(n)]


def store(n=12, code=CODE, **kw):
    return {"dong_code": code, "dong_name": "노형동", "weeks": weeks(n, **kw)}


def region(**dong_quality):
    r = json.loads(json.dumps(REGION))
    r["dongs"][CODE]["_quality"].update(dong_quality)
    return r


def test_F1_fit():
    f = F.judge(store(12), "노형동에서 카페 해요", REGION, AS_OF)
    assert (f.verdict, f.cases, f.mode, f.notes) == (F.FIT, ["F1"], "current", [])
    assert f.store_compare and f.use_foreign


def test_F2_non_cafe_narrows_to_region_only():
    f = F.judge(store(12), "노형동에서 고깃집 해요. 손님이 줄었어요", REGION, AS_OF)
    assert f.verdict == F.NARROW and "F2" in f.cases and not f.store_compare and f.mode == "region_only"
    assert "고깃집" in f.notes[0] and f.notes[0].startswith("[범위 축소]")


def test_F2_cafe_word_wins():   # "카페 옆 고깃집"처럼 카페가 함께 나오면 카페로 본다
    assert "F2" not in F.judge(store(12), "카페 옆에 고깃집이 생겼어요", REGION, AS_OF).cases


@pytest.mark.parametrize("n, have", [(4, "4주"), (11, "11주"), (0, "없음")])
def test_F3_short_history(n, have):
    f = F.judge(store(n), "카페", REGION, AS_OF)
    assert f.cases == ["F3"] and f.verdict == F.NARROW and f.mode == "region_only" and have in f.notes[0]


def test_F3_long_history_uses_latest_12_weeks():
    assert F.judge(store(16), "카페", REGION, AS_OF).cases == ["F1"]


def test_F4_outside_pilot_is_unable():
    f = F.judge(store(12, code="5011099999"), "카페", REGION, AS_OF)
    assert f.verdict == F.UNABLE and "F4" in f.cases and not f.has_region and f.mode == "region_only"
    assert "노형동" in f.notes[0]   # 시범 지역 안내


def test_F4_dominates_other_narrowing():
    f = F.judge(store(4, code=None), "카페", REGION, AS_OF)
    assert f.verdict == F.UNABLE and {"F4", "F3"} <= set(f.cases)


def test_F5_stale_region_keeps_diagnosis_but_notes():
    f = F.judge(store(12), "카페", REGION, AS_OF + timedelta(days=F.LAG_WARN_DAYS + 1))
    assert f.cases == ["F5"] and f.verdict == F.NARROW and f.store_compare and f.mode == "current"
    assert "2026-08-31까지" in f.notes[0]


def test_F5_within_tolerance_is_fine():
    assert F.judge(store(12), "카페", REGION, AS_OF + timedelta(days=F.LAG_WARN_DAYS)).cases == ["F1"]


def test_F6_foreign_gaps_drop_foreign_only():
    f = F.judge(store(12), "카페", region(foreign_gap_ratio=0.25), AS_OF)
    assert f.cases == ["F6"] and not f.use_foreign and f.store_compare and f.mode == "current"
    assert "내국인" in f.notes[0] and "%" not in f.notes[0]          # 사유 문장엔 % 수치 없음(C 검증과 충돌 방지)
    assert f.details["foreign_gap_ratio"] == 0.25                     # 정확한 값은 details(trace)로


def test_F7_store_period_not_overlapping_region():
    f = F.judge(store(12, last_monday=date(2026, 11, 2)), "카페", REGION, AS_OF)   # 점주가 '지금' 주문을 넣은 경우
    assert f.cases == ["F7"] and not f.store_compare and f.mode == "region_only" and "0일만 겹침" in f.notes[0]


def test_F7_boundary_21_days_is_ok():
    # 최근 6주가 지역 최근 창과 정확히 21일 겹치도록 3주 뒤로 민다 → 기준(21일) 충족
    assert "F7" not in F.judge(store(12, last_monday=date(2026, 9, 14)), "카페", REGION, AS_OF).cases
    assert "F7" in F.judge(store(12, last_monday=date(2026, 9, 21)), "카페", REGION, AS_OF).cases


def test_mock_style_region_skips_F5_F7():
    mock = json.loads((MOCK_DIR / "region_mock.json").read_text(encoding="utf-8"))
    f = F.judge(store(12, code="5011066000", last_monday=date(2030, 1, 7)), "카페", mock, None)
    assert f.cases == ["F1"]   # 날짜 없는 창·기준일 없음 → 기간 판정 생략


# ── 그래프 전체(mock): 판정이 진단·근거·사유에 반영되는지
@pytest.mark.skipif(not USE_MOCK, reason="mock 모드 테스트")
def test_graph_non_cafe_gives_region_only_without_store_diagnosis():
    from coach.graph import run
    si = next(c for c in json.loads((MOCK_DIR / "synthetic_stores.json").read_text(encoding="utf-8"))
              if c["case_id"] == "S-only_me")["store_input"]
    out = run("노형동 고깃집인데 손님이 줄었어요", si)
    ar = out["analysis_result"]
    assert ar["mode"] == "region_only" and ar["diagnosis"] == "undetermined"
    assert ar["metrics"]["my_orders_change_pct"] is None and "E04" not in {e["claim_id"] for e in ar["evidence"]}
    assert any(n.startswith("[범위 축소]") and "고깃집" in n for n in ar["notes"])
    assert any("고깃집" in s["text"] for s in out["report"]["limits"])      # C 리포트 '한계'로 점주에게 안내됨
    fit = next(t for t in out["trace"] if t["node"] == "A.check_fitness")
    assert fit["fitness"] == F.NARROW and fit["cases"] == ["F2"]


@pytest.mark.skipif(not USE_MOCK, reason="mock 모드 테스트")
def test_graph_E04_period_is_store_dates():
    from coach.graph import run
    si = next(c for c in json.loads((MOCK_DIR / "synthetic_stores.json").read_text(encoding="utf-8"))
              if c["case_id"] == "S-only_me")["store_input"]
    out = run("노형동 카페 손님이 줄었어요", si)
    e04 = next(e for e in out["analysis_result"]["evidence"] if e["claim_id"] == "E04")
    assert e04["period"] == "2026-09-28~2026-11-08 vs 2026-08-17~2026-09-27"   # 동네 창 라벨이 아니라 가게 실제 기간
    assert out["analysis_result"]["diagnosis"] == "only_me" and out["verify_result"]["passed"]


# ── S9: 평가 가능 여부(지역 숫자만으로 그 진단이 나올 수 있는가) — 가게 결과는 보지 않는다
def test_reachable_diagnoses_depends_only_on_region():
    from coach.nodes.a_data import reachable_diagnoses
    assert reachable_diagnoses(-14.0, -12.0, 1.5) == {"co_decline", "undetermined"}  # 동네 감소 → 동반 하락(나만 하락은 불가)
    assert "season_weather" in reachable_diagnoses(-18.0, -2.0, 4.2)                # 직전 급감·전년 유지 → 계절 가능
    assert reachable_diagnoses(41.1, -4.7, -0.6) == {"only_me", "undetermined"}       # 성수기(실데이터 애월) → 둘만


@pytest.mark.parametrize("make", [
    lambda: F.judge(store(12), "노형동 고깃집", REGION, AS_OF),                                  # F2
    lambda: F.judge(store(4), "카페", REGION, AS_OF),                                           # F3
    lambda: F.judge(store(12, code="5011099999"), "카페", REGION, AS_OF),                        # F4
    lambda: F.judge(store(12), "카페", REGION, AS_OF + timedelta(days=30)),                      # F5
    lambda: F.judge(store(12), "카페", region(foreign_gap_ratio=0.31), AS_OF),                  # F6
    lambda: F.judge(store(12, last_monday=date(2026, 11, 2)), "카페", REGION, AS_OF),            # F7
])
def test_notes_have_no_percent_numbers(make):
    """사유는 리포트 '한계'에 근거 번호 없이 실림 → '숫자%' 가 있으면 C 검증이 문장을 지운다."""
    import re
    assert not any(re.search(r"\d+(?:\.\d+)?\s*%", n) for n in make().notes)
