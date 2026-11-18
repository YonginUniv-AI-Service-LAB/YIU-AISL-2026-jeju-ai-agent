"""평가 실행 기록기 — 결과 하나 = 코드 버전 + 데이터 버전 + 설정 + 환경 (CLAUDE.md §6). 노드(A·B·C) 공용.

write_run(node, predictions, metrics, snapshot_ids) →
  eval/runs/<UTC시각>_<커밋>_<노드>/
    run_meta.json     코드(커밋·dirty), 데이터(snapshot_id), 설정(config + config_hash), 환경(Python·OS·lock 해시)
    metrics.json      지표
    predictions.jsonl 문항별 예측(문항 id 순) → predictions_sha256 이 같으면 같은 결과(재현 확인)
  eval/runs/index.jsonl 에 한 줄 추가(실행끼리 비교용)
기록 위치: 평소 실행(reproduce·eval)은 eval/runs/local/ (git 제외 → 작업 폴더가 더러워지지 않음),
          공식 기록은 COACH_EVAL_OFFICIAL=1 (python tasks.py eval --official) 일 때만 eval/runs/ (커밋 대상). #11 리뷰
MLflow 가 설치돼 있으면(requirements-dev.txt) 같은 내용을 로컬 MLflow(mlruns/)에도 기록. 없으면 조용히 건너뜀.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "eval" / "runs"            # 공식 기록(커밋)
RUNS_LOCAL = RUNS / "local"               # 평소 실행 기록(.gitignore)


def default_root() -> Path:
    return RUNS if os.getenv("COACH_EVAL_OFFICIAL") == "1" else RUNS_LOCAL


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return ""


def code_version() -> dict:
    commit = _git("rev-parse", "--short", "HEAD") or "unknown"
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
    return {"commit": commit, "dirty": dirty, "branch": _git("rev-parse", "--abbrev-ref", "HEAD")}


def config_snapshot() -> dict:
    """결과에 영향을 주는 설정 전부(임계값·비교 창·강수 기준·판정 기준값·관측소 매핑)."""
    from coach.config import THRESHOLDS
    from coach.data import fitness as F
    cfg = {"thresholds": THRESHOLDS,
           "fitness": {k: getattr(F, k) for k in ("STORE_WEEKS", "MIN_STORE_WEEKS", "MIN_OVERLAP_DAYS",
                                                   "LAG_WARN_DAYS", "FOREIGN_GAP_MAX_RATIO")}}
    try:
        from coach.data import region_real as R
        cfg["region"] = {"as_of": str(R.AS_OF), "weeks": R.WEEKS, "yoy_shift_days": R.YOY_SHIFT_DAYS,
                         "rain_mm": R.RAIN_MM, "max_missing_ratio": R.MAX_MISSING_RATIO, "station_of": R.STATION_OF}
    except ImportError:   # pandas 없는 환경(B·C 평가만 돌릴 때)
        pass
    return cfg


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def environment() -> dict:
    lock = ROOT / "requirements.lock"
    return {"python": sys.version.split()[0], "platform": platform.platform(),
            "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest()[:12] if lock.exists() else None,
            "env": {k: os.environ[k] for k in sorted(os.environ) if k.startswith("COACH_")}}


def write_run(node: str, predictions: list[dict], metrics: dict, snapshot_ids: list[str], extra: dict | None = None,
              root: Path | None = None) -> Path:
    root = root or default_root()
    root.mkdir(parents=True, exist_ok=True)
    code, cfg = code_version(), config_snapshot()
    preds = sorted(predictions, key=lambda p: (p.get("split", ""), p["id"]))
    meta = {
        "node": node, "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "code": code, "data": {"snapshot_ids": sorted(set(snapshot_ids))},
        "config": cfg, "config_hash": _sha(cfg)[:12], "environment": environment(),
        "predictions_sha256": _sha(preds)[:16], "official": root == RUNS, **(extra or {}),
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = root / f"{stamp}_{code['commit']}{'-dirty' if code['dirty'] else ''}_{node}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "predictions.jsonl").write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in preds), encoding="utf-8")
    flat = {k: v for k, v in metrics.items() if isinstance(v, (int, float))}
    with (root / "index.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"run": d.name, "node": node, "commit": code["commit"], "dirty": code["dirty"],
                            "snapshot_ids": meta["data"]["snapshot_ids"], "config_hash": meta["config_hash"],
                            "predictions_sha256": meta["predictions_sha256"], **flat}, ensure_ascii=False) + "\n")
    _log_mlflow(d, node, meta, metrics)
    return d


def _log_mlflow(run_dir: Path, node: str, meta: dict, metrics: dict) -> None:
    """MLflow 가 있으면 로컬 DB(mlflow.db)·아티팩트(mlruns/)에 기록. 실패해도 평가는 멈추지 않는다."""
    if os.getenv("COACH_MLFLOW", "1") == "0":
        return
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")   # import 시 안내 문구 끄기
    try:
        import mlflow
    except ImportError:
        return
    try:
        mlflow.set_tracking_uri(f"sqlite:///{(ROOT / 'mlflow.db').as_posix()}")
        exp = mlflow.get_experiment_by_name("jeju-coach-eval") or mlflow.get_experiment(
            mlflow.create_experiment("jeju-coach-eval", artifact_location=(ROOT / "mlruns").as_uri()))
        with mlflow.start_run(experiment_id=exp.experiment_id, run_name=run_dir.name):
            mlflow.set_tags({"node": node, "commit": meta["code"]["commit"], "dirty": str(meta["code"]["dirty"]),
                             "snapshot_ids": ",".join(meta["data"]["snapshot_ids"])})
            mlflow.log_params({"config_hash": meta["config_hash"], "predictions_sha256": meta["predictions_sha256"],
                               "python": meta["environment"]["python"], "lock_sha256": meta["environment"]["lock_sha256"],
                               **{f"thr.{k}": v for k, v in meta["config"]["thresholds"].items()}})
            mlflow.log_metrics({k: float(v) for k, v in metrics.items() if isinstance(v, (int, float))})
            mlflow.log_artifacts(str(run_dir))
        print(f"  (MLflow 기록: experiment jeju-coach-eval / run {run_dir.name} → `mlflow ui --backend-store-uri sqlite:///mlflow.db`)")
    except Exception as e:  # MLflow 문제로 평가가 실패하면 안 됨
        print(f"  (MLflow 기록 건너뜀: {type(e).__name__}: {str(e)[:120]})")
