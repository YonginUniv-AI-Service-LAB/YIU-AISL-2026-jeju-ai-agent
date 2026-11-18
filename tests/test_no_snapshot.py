"""★A 스냅샷이 없을 때도 멈추지 않는지 — 제출본·새 환경에서 스냅샷 폴더가 없거나, 라이브 조회만 가능한 경우."""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import pytest

pytest.importorskip("pandas")
from coach.config import MOCK_DIR  # noqa: E402
from coach.data import region_live as L  # noqa: E402
from coach.data import region_real as R  # noqa: E402
from coach.graph import run  # noqa: E402
from coach.nodes import a_data as A  # noqa: E402

SNAP_DIR = R.SNAPSHOT_ROOT / str(R.AS_OF)
SI = {**json.loads((MOCK_DIR / "synthetic_stores_asof_20260831.json").read_text(encoding="utf-8"))[1]["store_input"],
      "budget_krw": 200000, "staff_am": 1, "staff_pm": 1}


@pytest.fixture
def no_snapshot(monkeypatch, tmp_path):
    """기준일 스냅샷이 없는 상태를 흉내 낸다(load_snapshot 이 FileNotFoundError)."""
    def missing(*a, **k):
        raise FileNotFoundError("스냅샷 없음(테스트)")
    monkeypatch.setattr(R, "load_snapshot", missing)
    monkeypatch.setattr(A, "USE_MOCK", False)
    monkeypatch.setattr(L, "LIVE_DIR", tmp_path)
    A._real_region.cache_clear(); A._live_region.cache_clear()
    yield
    A._real_region.cache_clear(); A._live_region.cache_clear()


def _fitness(out):
    return next(t for t in out["trace"] if t["node"] == "A.check_fitness")


def test_snapshot_mode_without_snapshot_answers_instead_of_crashing(no_snapshot, monkeypatch):
    monkeypatch.setattr(A, "USE_LIVE", False)
    out = run("노형동에서 카페 해요. 손님이 줄었어요", SI)
    ar = out["analysis_result"]
    assert ar["diagnosis"] == "undetermined" and ar["snapshot_id"] == "none" and ar["mode"] == "region_only"
    assert any(n.startswith("[판정 불가]") and "스냅샷" in n for n in ar["notes"])
    assert _fitness(out)["fitness"] == "판정 불가" and out["report"] is not None


class _Broken:
    def latest(self, *a):
        raise RuntimeError("753 호출 실패: HTTPError: 403 Forbidden")


def test_live_failure_and_no_snapshot_answers_instead_of_crashing(no_snapshot, monkeypatch):
    monkeypatch.setattr(A, "USE_LIVE", True)
    monkeypatch.setattr(L, "FETCHER_FACTORY", lambda today=None: _Broken())
    out = run("노형동에서 카페 해요. 손님이 줄었어요", SI)
    notes = out["analysis_result"]["notes"]
    assert any(n.startswith("[판정 불가] 실시간 동네 데이터 조회 실패") and "403" in n for n in notes)
    assert out["analysis_result"]["diagnosis"] == "undetermined"


@pytest.mark.skipif(not (SNAP_DIR / "manifest.json").exists(), reason="재생할 데이터(스냅샷 폴더) 없음")
def test_live_success_does_not_need_snapshot(no_snapshot, monkeypatch):
    """라이브 조회가 되면 기준일 스냅샷 없이도 정상 진단(데이터는 재생 fetcher 가 API 대신 공급)."""
    monkeypatch.setattr(A, "USE_LIVE", True)
    monkeypatch.setattr(L, "FETCHER_FACTORY", lambda today=None: L.FrozenFetcher(SNAP_DIR))
    out = run("노형동에서 카페 해요. 손님이 줄었어요", SI)
    ar = out["analysis_result"]
    assert _fitness(out)["region_source"] == "live" and ar["diagnosis"] == "only_me" and out["verify_result"]["passed"]
