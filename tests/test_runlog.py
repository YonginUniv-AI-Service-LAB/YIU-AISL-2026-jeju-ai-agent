"""★A 실행 기록기 테스트 — 같은 예측이면 같은 지문, 기록 파일 3개 + index 한 줄."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))
import runlog  # noqa: E402


def test_write_run_records_four_parts_and_stable_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_MLFLOW", "0")
    preds = [{"id": "A02", "split": "eval", "x": 1}, {"id": "A01", "split": "eval", "x": 2}]
    d1 = runlog.write_run("A", preds, {"acc": 1.0}, ["snap1"], root=tmp_path)
    d2 = runlog.write_run("A", list(reversed(preds)), {"acc": 1.0}, ["snap1"], root=tmp_path)   # 순서만 다름
    m1, m2 = (json.loads((d / "run_meta.json").read_text(encoding="utf-8")) for d in (d1, d2))
    assert m1["predictions_sha256"] == m2["predictions_sha256"]              # 순서와 무관하게 같은 결과 → 같은 지문
    assert {"code", "data", "config", "config_hash", "environment"} <= set(m1)   # 코드·데이터·설정·환경
    assert {p.name for p in d1.iterdir()} == {"run_meta.json", "metrics.json", "predictions.jsonl"}
    assert [json.loads(x)["id"] for x in (d1 / "predictions.jsonl").read_text(encoding="utf-8").splitlines()] == ["A01", "A02"]
    idx = (tmp_path / "index.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(idx) == 2 and json.loads(idx[0])["acc"] == 1.0


def test_default_root_is_local_unless_official(monkeypatch):
    """#11 리뷰: reproduce 가 커밋된 eval/runs/index.jsonl 을 바꾸면 작업 폴더가 더러워짐 → 평소엔 local."""
    monkeypatch.delenv("COACH_EVAL_OFFICIAL", raising=False)
    assert runlog.default_root() == runlog.RUNS_LOCAL
    monkeypatch.setenv("COACH_EVAL_OFFICIAL", "1")
    assert runlog.default_root() == runlog.RUNS
