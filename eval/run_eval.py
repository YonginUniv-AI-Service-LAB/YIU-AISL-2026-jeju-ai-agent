"""★C 평가 하네스 — C 문항 채점 + C 지표(A·C 문항 리포트 전체) + 노드별 지표 표 + 실행 기록(runlog).

사용: python tasks.py eval   (또는 COACH_MOCK=1 COACH_LLM=0 python eval/run_eval.py)
- C 문항: eval/cases_c.jsonl (C01~C05). 정답은 하네스 구현 전에 고정(decisions_C ⑤), 규칙 조정에 쓰지 않음
- C 지표(합성 사례 N개 기준, 실제 점주 대상 아님):
    unsupported_sentence_rate  최종 리포트의 수치 문장 중 근거(claim_id) 없는·잘못된 문장 비율
    constraint_violation_rate  최종 추천·대안 실험 중 점주 조건(예산·인력·변경 가능 항목)을 넘는 비율
    regen_improved_rate        첫 검증에서 걸린 리포트 중 재생성 뒤 위반이 줄어든 비율(regen_resolved_rate: 끝내 통과)
- A 문항 리포트는 실데이터가 필요해 별도 프로세스(COACH_MOCK=0, --a-reports)로 만들고, 스냅샷이 없으면 건너뜀
- 노드별 표: A = 최신 A 실행 기록(eval/runs·local index), B = eval/run_eval_b.py 출력, C = 이번 실행
결과: eval/runs/local/<시각>_<커밋>_C/ (COACH_EVAL_OFFICIAL=1 이면 eval/runs/)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))
if hasattr(sys.stdout, "reconfigure"):  # pytest 캡처 객체에는 없음
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("COACH_LLM", "0")

from coach.config import MOCK_DIR, USE_MOCK  # noqa: E402
from coach.contracts import StoreInput  # noqa: E402
from coach.graph import run  # noqa: E402
from coach.nodes import c_experiment as C  # noqa: E402

PUBLIC = lambda rep: {k: v for k, v in (rep or {}).items() if not k.startswith("_")}  # noqa: E731
_PCT = re.compile(r"\d+(?:\.\d+)?(?=\s*%)")


# ───────────────────────── 리포트 하나의 C 지표 재료
def c_facts(out: dict) -> dict:
    rep, ar, si = out.get("report") or {}, out.get("analysis_result") or {}, out.get("store_input") or {}
    evd = {e["claim_id"] for e in ar.get("evidence", [])}
    a_notes = {C.note_sentence(n) for n in ar.get("notes", [])} | set(ar.get("notes", []))
    numeric = unsupported = 0
    for sec in C.SECTIONS:
        for s in rep.get(sec, []):
            if sec == "limits" and s["text"] in a_notes:
                continue
            if _PCT.search(s["text"]):
                numeric += 1
                unsupported += not s["claim_ids"] or any(c not in evd for c in s["claim_ids"])
    cat = {x["id"]: x for x in C._catalog()}
    exps = [e for e in [rep.get("experiment"), *rep.get("alternatives", [])] if e]
    sio = StoreInput.model_validate(si) if si else None
    bad = sum(1 for e in exps if e["catalog_id"] not in cat or (sio and C.infeasible_reasons(cat[e["catalog_id"]], sio)))
    ver = [t for t in out.get("trace", []) if t["node"] == "C.verify"]
    return {"numeric_sentences": numeric, "unsupported_sentences": unsupported, "experiments": len(exps),
            "constraint_violations": bad, "verify_seq": [t["passed"] for t in ver],
            "verify_n_violations": [t["n_violations"] for t in ver],
            "first_violation_types": ver[0].get("types", []) if ver else [], "status": out.get("status")}


def c_metrics(facts: list[dict]) -> dict:
    num = sum(f["numeric_sentences"] for f in facts)
    exps = sum(f["experiments"] for f in facts)
    regen = [f for f in facts if f["verify_seq"] and not f["verify_seq"][0]]
    improved = [f for f in regen if f["verify_n_violations"][-1] < f["verify_n_violations"][0]
                or f["status"] == "done_with_drops"]
    rate = lambda a, b: round(a / b, 4) if b else None  # noqa: E731
    return {"reports_n": len(facts), "numeric_sentences_n": num,
            "unsupported_sentence_rate": rate(sum(f["unsupported_sentences"] for f in facts), num),
            "experiments_n": exps, "constraint_violation_rate": rate(sum(f["constraint_violations"] for f in facts), exps),
            "regen_n": len(regen), "regen_improved_rate": rate(len(improved), len(regen)),
            "regen_resolved_rate": rate(sum(f["verify_seq"][-1] for f in regen), len(regen))}


# ───────────────────────── C 문항 채점
def build_store(spec: dict) -> dict:
    base = {c["case_id"]: c["store_input"] for c in json.loads((MOCK_DIR / "synthetic_stores.json").read_text(encoding="utf-8"))}
    return {**base[spec["base"]], **spec.get("override", {})}


def grade(c: dict, out: dict, f: dict) -> dict:
    exp, rep = c["expect"], out.get("report") or {}
    text = json.dumps(PUBLIC(rep), ensure_ascii=False)
    cat = {x["id"]: x for x in C._catalog()}
    exps = [e for e in [rep.get("experiment"), *rep.get("alternatives", [])] if e]
    lim = [s["text"] for s in rep.get("limits", [])]
    notes = (out.get("analysis_result") or {}).get("notes", [])

    def within(w):
        items = [cat[e["catalog_id"]] for e in exps]
        return all(x["cost_krw"] <= w["max_cost_krw"] and x["min_staff_am"] <= w["max_min_staff_am"]
                   and x["min_staff_pm"] <= w["max_min_staff_pm"] and set(x["requires"]) <= set(w["requires_subset"])
                   for x in items)
    checks = {
        "constraint_violations": lambda v: f["constraint_violations"] == v,
        "experiments_within": within,
        "experiment": lambda v: rep.get("experiment") == v,
        "alternatives": lambda v: len(rep.get("alternatives", [])) == v,
        "to_check_contains": lambda v: any(v in s["text"] for s in rep.get("to_check", [])),
        "must_not_contain": lambda v: not any(p in text for p in v),
        "must_contain": lambda v: all(p in text for p in v),
        "verify_passed": lambda v: bool((out.get("verify_result") or {}).get("passed")) == v,
        "mode": lambda v: rep.get("mode") == v,
        "limits_contains": lambda v: all(any(p in t for t in lim) for p in v),
        "a_notes_kept": lambda v: (bool(notes) and all(C.note_sentence(n) in lim for n in notes)) == v,
        "first_verify_violation_types": lambda v: set(v) <= set(f["first_violation_types"]),
        "verify_seq": lambda v: f["verify_seq"] == v,
        "final_status": lambda v: out.get("status") == v,
    }
    correct = {k: bool(checks[k](v)) for k, v in exp.items()}
    return {"correct": correct, "pass": all(correct.values())}


def run_c_cases() -> list[dict]:
    preds = []
    for line in (ROOT / "eval" / "cases_c.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        extra = {"debug_inject": c["inject"]} if c.get("inject") else {}
        out = run(c["question"], build_store(c["store"]), **extra)
        f = c_facts(out)
        preds.append({"id": c["id"], "split": "eval", "node": "C", "tags": c.get("tags", []), "point": c["point"],
                      "expected": c["expect"], **grade(c, out, f), "facts": f,
                      "snapshot_id": (out.get("analysis_result") or {}).get("snapshot_id"),
                      "experiment": (out["report"].get("experiment") or {}).get("catalog_id") if out.get("report") else None})
    return preds


# ───────────────────────── A 문항 리포트(실데이터, 별도 프로세스)
def a_reports_child() -> None:
    """COACH_MOCK=0 에서만. A 문항을 A 하네스의 함수로 돌려 리포트의 C 지표 재료만 JSON 한 줄로 출력."""
    import run_eval_a as RA  # A 소유 — 가져다 쓰기만 함
    rows = []
    for line in (ROOT / "eval" / "cases_a.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        try:
            out = RA.run_case(c["question"], RA.build_store(c["store"]), c.get("region", {}))
        except FileNotFoundError as e:
            rows.append({"id": c["id"], "skipped": str(e)})
            continue
        rows.append({"id": c["id"], "facts": c_facts(out), "snapshot_id": (out.get("analysis_result") or {}).get("snapshot_id")})
    print("@@A_REPORTS@@" + json.dumps(rows, ensure_ascii=False))


def a_reports() -> tuple[list[dict], str | None]:
    env = {**os.environ, "COACH_MOCK": "0", "COACH_LLM": "0", "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, __file__, "--a-reports"], cwd=ROOT, env=env, capture_output=True,
                       text=True, encoding="utf-8")
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("@@A_REPORTS@@")), None)
    if r.returncode or not line:
        return [], (r.stderr.strip().splitlines() or ["알 수 없는 오류"])[-1][:200]
    return json.loads(line[len("@@A_REPORTS@@"):]), None


# ───────────────────────── 노드별 표
def latest_a() -> dict | None:
    """공식(eval/runs)·로컬(eval/runs/local) 기록 중 가장 최근 A 실행(실행 이름이 UTC 시각으로 시작)."""
    import runlog
    rows = []
    for root in (runlog.RUNS, runlog.RUNS_LOCAL):
        idx = root / "index.jsonl"
        if idx.exists():
            rows += [json.loads(ln) | {"_root": root.relative_to(ROOT).as_posix()}
                     for ln in idx.read_text(encoding="utf-8").splitlines() if ln.strip()]
    rows = [x for x in rows if x.get("node") == "A"]
    return max(rows, key=lambda x: x["run"]) if rows else None


def b_summary() -> list[str]:
    env = {**os.environ, "COACH_MOCK": "1", "COACH_LLM": "0", "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, str(ROOT / "eval" / "run_eval_b.py")], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8")
    return [ln.strip() for ln in r.stdout.splitlines() if "정확도" in ln] or [f"실행 실패: {r.stderr.strip()[-120:]}"]


def main() -> None:
    import runlog
    if USE_MOCK is False:
        sys.exit("C 평가는 mock(COACH_MOCK=1)에서 시작합니다. A 문항 리포트는 하네스가 따로 실데이터로 돌립니다.")
    preds = run_c_cases()
    a_rows, a_err = a_reports()
    for r in a_rows:
        if "facts" in r:
            preds.append({"id": r["id"], "split": "a_reports", "node": "C", "facts": r["facts"], "snapshot_id": r["snapshot_id"]})
    ev = [p for p in preds if p["split"] == "eval"]
    met = {"c_cases_n": len(ev), "c_cases_pass_rate": round(sum(p["pass"] for p in ev) / len(ev), 4) if ev else None,
           **{f"c_{k}" if not k.startswith("c_") else k: v for k, v in c_metrics([p["facts"] for p in preds]).items()},
           "a_reports_n": sum(p["split"] == "a_reports" for p in preds),
           "a_reports_skipped": [r["id"] for r in a_rows if "skipped" in r] + ([f"전체: {a_err}"] if a_err else []),
           "note": "합성 사례 기준. 실제 점주 대상 지표 아님"}

    print("■ C 평가 (합성 사례 기준, 실제 점주 대상 아님)")
    print("| id | 태그 | 확인 항목 | 결과 |")
    print("|---|---|---|---|")
    for p in ev:
        bad = [k for k, ok in p["correct"].items() if not ok]
        print(f"| {p['id']} | {','.join(p['tags'])} | {', '.join(p['correct'])} | {'✅' if p['pass'] else '❌ ' + ', '.join(bad)} |")
    print(f"\nC 문항 {met['c_cases_n']}개 통과율 {met['c_cases_pass_rate']}")
    print(f"C 지표 (리포트 {met['c_reports_n']}개 = C 문항 {len(ev)} + A 문항 {met['a_reports_n']}"
          + (f", 건너뜀 {met['a_reports_skipped']}" if met["a_reports_skipped"] else "") + ")")
    print(f"  근거 없는 문장 비율 {met['c_unsupported_sentence_rate']} (수치 문장 {met['c_numeric_sentences_n']}개)")
    print(f"  점주 조건 위반 비율 {met['c_constraint_violation_rate']} (추천·대안 {met['c_experiments_n']}개)")
    print(f"  재생성 후 개선 비율 {met['c_regen_improved_rate']} · 끝내 통과 {met['c_regen_resolved_rate']} (재생성 {met['c_regen_n']}건)")

    a, b = latest_a(), b_summary()
    print("\n■ 노드별 지표 (합성 사례 기준)")
    print("| 노드 | 지표 | 출처 |")
    print("|---|---|---|")
    if a:
        print(f"| A 데이터·분석 | 적합성 판정 {a.get('eval_fitness_accuracy')} · 진단 {a.get('eval_diagnosis_accuracy')} · "
              f"모드 {a.get('eval_mode_accuracy')} (문항 {a.get('eval_n')}개, 건너뜀 {a.get('eval_skipped')}) | "
              f"{a['_root']}/{a['run']} |")
    else:
        print("| A 데이터·분석 | 실행 기록 없음 → `python tasks.py eval` | - |")
    print(f"| B 오케스트레이터 | {' · '.join(b)} | eval/run_eval_b.py (규칙 기반) |")
    print(f"| C 실험·검증 | 문항 통과 {met['c_cases_pass_rate']} · 근거 없는 문장 {met['c_unsupported_sentence_rate']} · "
          f"조건 위반 {met['c_constraint_violation_rate']} · 재생성 후 개선 {met['c_regen_improved_rate']} | 이번 실행 |")
    met["node_table"] = {"A": {k: a.get(k) for k in ("run", "eval_fitness_accuracy", "eval_diagnosis_accuracy",
                                                    "eval_mode_accuracy", "eval_n")} if a else None, "B": b}
    d = runlog.write_run("C", preds, met, [p["snapshot_id"] for p in preds if p.get("snapshot_id")])
    print(f"→ 실행 기록 {d.relative_to(ROOT)}")


if __name__ == "__main__":
    a_reports_child() if "--a-reports" in sys.argv else main()
