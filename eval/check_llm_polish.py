"""C 리포트 LLM 다듬기 실점검(합성 점주 4명). 키는 .env 의 LLM_API_KEY 에서만 읽고 출력하지 않는다.

    PowerShell:  $env:COACH_MOCK="1"; $env:COACH_LLM="1"; .venv\\Scripts\\python eval/check_llm_polish.py
결과: eval/llm_polish_check.json (문장 전후·재생성 흐름). 정답 판정이 아니라 동작 확인용.
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from coach.config import MOCK_DIR  # noqa: E402
from coach.graph import run  # noqa: E402
from coach.llm import client as llm  # noqa: E402

def _mask(s) -> str:
    """오류 문구에 키가 섞여도 출력·파일에 남지 않게 가림."""
    s, key = str(s), os.getenv("LLM_API_KEY") or ""
    s = s.replace(key, "<KEY>") if key else s
    return re.sub(r"AIza[0-9A-Za-z_\-]{10,}", "<KEY>", s)[:160]


rows = []
for c in json.loads((MOCK_DIR / "synthetic_stores.json").read_text(encoding="utf-8")):
    out = run("손님이 줄었어요", c["store_input"], llm_available=True)
    comp = [t for t in out["trace"] if t["node"] == "C.compose_report"]
    ver = [t["passed"] for t in out["trace"] if t["node"] == "C.verify"]
    pol = out["report"].get("_polish") or {}
    b_err = [t.get("llm_error") for t in out["trace"] if t["node"] == "B.parse_input"][0]
    errs = [_mask(e) for e in [b_err] + [(t.get("polish") or {}).get("error") for t in comp] if e]
    rows.append({"case": c["case_id"], "status": out.get("status"), "verify_seq": ver,
                 "polish_seq": [{k: (_mask(v) if k == "error" else v) for k, v in (t.get("polish") or {}).items()}
                                for t in comp],
                 "errors": errs, "pairs": list(zip(pol.get("template", []), pol.get("texts", [])))})
    print(f"{c['case_id']}: verify {ver} / polish {[(t.get('polish') or {}).get('action') for t in comp]}"
          + (f" / 오류 {errs}" if errs else ""))
    for a, b in rows[-1]["pairs"]:
        if a != b:
            print(f"   - {a}\n   + {b}")
print(f"provider={llm.provider()} model={llm.model_name()}")
(ROOT / "eval" / "llm_polish_check.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
