"""B 세션 저장·재방문 테스트 — 임시 폴더에 저장해서 실제 data/ 를 건드리지 않는다."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pytest

from coach import session as S
from coach.config import MOCK_DIR

CASE = json.loads((MOCK_DIR / "synthetic_stores.json").read_text(encoding="utf-8"))[0]["store_input"]


@pytest.fixture(autouse=True)
def tmp_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "SESSION_DIR", tmp_path)


def test_first_visit_saves_experiment():
    out = S.run_session("cafe01", "손님이 줄었어요", CASE)
    assert out.get("experiment_review") is None            # 첫 방문은 점검 없음
    assert S.load_previous("cafe01")["catalog_id"] == out["report"]["experiment"]["catalog_id"]


def test_revisit_reviews_previous_experiment():
    first = S.run_session("cafe01", "손님이 줄었어요", CASE)
    out = S.run_session("cafe01", "2주 지났어요, 어땠나요?", CASE)
    rv = out["experiment_review"]
    assert rv["catalog_id"] == first["report"]["experiment"]["catalog_id"]   # 직전 실험을 점검
    assert rv["verdict"] in ("개선 신호", "악화 신호", "확인 불가")


def test_ask_back_is_not_saved():
    out = S.run_session("cafe02", "카페 매출이 줄었어요")   # 조건 부족 → 되묻기
    assert out["status"] == "need_input" and S.load_previous("cafe02") is None


@pytest.mark.parametrize("bad", ["../etc", "a/b", "", "카페"])
def test_bad_session_id_rejected(bad):
    with pytest.raises(ValueError):
        S.load_previous(bad)