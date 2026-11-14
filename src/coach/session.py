"""B 세션 저장 — 2주 후 재방문 흐름.

첫 방문: 실험을 추천받으면 세션 파일에 "어떤 실험을 언제 시작했는지"를 저장.
재방문: 같은 세션 코드로 오면 직전 실험을 불러와 previous_experiment 로 넣고 그래프 실행
        → B.review_experiment 가 실험 전·후 2주를 비교해 점검.

저장 위치: data/sessions/<session_id>.json  (git 에 올리지 않음 — .gitignore)
저장 내용: 가게 조건·실험·진단만. 점주 질문 원문은 저장하지 않음(개인정보 최소화).
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from .config import ROOT
from .graph import run

SESSION_DIR = ROOT / "data" / "sessions"
_ID_OK = re.compile(r"^[A-Za-z0-9_-]{1,40}$")  # 경로 조작 방지: 영문·숫자·_- 만


def _path(session_id: str) -> Path:
    if not _ID_OK.match(session_id or ""):
        raise ValueError("세션 코드는 영문·숫자·_- 1~40자만 쓸 수 있어요.")
    return SESSION_DIR / f"{session_id}.json"


def load_previous(session_id: str) -> dict | None:
    """직전 방문에서 저장한 실험. 없으면 None (= 첫 방문)."""
    p = _path(session_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8")).get("experiment")


def save_session(session_id: str, out: dict, today: str | None = None) -> Path | None:
    """실험이 추천된 경우에만 저장. 되묻기 중이면 저장하지 않음."""
    exp = (out.get("report") or {}).get("experiment")
    if out.get("status") == "need_input" or not exp:
        return None
    si = out.get("store_input") or {}
    data = {
        "session_id": session_id,
        "saved_at": today or date.today().isoformat(),
        "dong_code": si.get("dong_code"),
        "diagnosis": out["report"].get("diagnosis"),
        "experiment": {**exp, "started_at": today or date.today().isoformat()},
    }
    p = _path(session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def run_session(session_id: str, question: str, store_input: dict | None = None, **extra) -> dict:
    """세션 단위 실행: 직전 실험 불러오기 → 그래프 실행 → 새 실험 저장."""
    prev = load_previous(session_id)
    out = run(question, store_input, session_id=session_id, previous_experiment=prev, **extra)
    save_session(session_id, out)
    return out