"""A 평가 — 데이터 적합성 판정 정확도 + 진단 유형 정확도 (합성 사례 기준, 실제 점주 대상 정확도 아님).

사용: python tasks.py eval   (또는 COACH_MOCK=0 COACH_LLM=0 python eval/run_eval_a.py)
- split=eval : eval/cases_a.jsonl (A01~A07). 정답은 실행 전에 규칙·데이터 사실로 정함, 임계값 조정에 쓰지 않음
- split=dev  : 합성 점주 4유형(synthetic_stores_asof_20260831.json). 기대 유형이 그 동네·기준일에 나올 수 없으면 '평가 불가'
- region.source = snapshot(기준일 스냅샷) | replay(라이브 조회 때 동결한 데이터를 네트워크 없이 재생)
결과: eval/runs/<시각>_<커밋>_A/ (run_meta·metrics·predictions) + index.jsonl, MLflow 있으면 같이 기록
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("COACH_MOCK", "0")
os.environ.setdefault("COACH_LLM", "0")

from coach.config import MOCK_DIR, USE_MOCK  # noqa: E402
from coach.graph import run  # noqa: E402
from coach.nodes import a_data as A  # noqa: E402

import runlog  # noqa: E402

if USE_MOCK:
    sys.exit("A 평가는 실데이터(COACH_MOCK=0)로 돌립니다.")

from coach.data import region_live as L  # noqa: E402


def build_store(spec: dict) -> dict:
    """직전 주들 1000건/주 → 최근 6주 (1+변화율)배. 영업일 6일 고정이라 영업일당 변화율 = 지정값."""
    codes = A.dong_codes()
    last = date.fromisoformat(spec["last_monday"])
    n = spec["n_weeks"]
    recent = round(1000 * (1 + spec["my_change_pct"] / 100))
    weeks = [{"week_start": str(last - timedelta(weeks=n - 1 - i)), "orders": recent if i >= n - 6 else 1000,
              "open_days": 6, "sales_krw": 0} for i in range(n)]
    return {"dong_name": spec["dong"], "dong_code": spec.get("dong_code") or codes.get(spec["dong"]),
            "budget_krw": 200000, "staff_am": 1, "staff_pm": 1, "changeable": ["menu", "price"],
            "weeks": weeks, "is_synthetic": True}


def run_case(question: str, si: dict, region: dict) -> dict:
    """그래프 전체(B→A→C) 실행. replay 면 라이브 모드를 켜고 fetcher 를 동결 데이터 재생기로 바꿔 끼운다."""
    if region.get("source") != "replay":
        return run(question, si)
    src = ROOT / region["dir"]
    if not (src / "manifest.json").exists():
        raise FileNotFoundError(f"동결 데이터 없음: {region['dir']} (라이브 조회 후 생김 · 팀 공유 폴더로 받기)")
    old = (A.USE_LIVE, L.FETCHER_FACTORY, L.LIVE_DIR)
    A.USE_LIVE, L.LIVE_DIR = True, Path(tempfile.mkdtemp())          # 재생 결과를 원본 폴더에 덮어쓰지 않음
    L.FETCHER_FACTORY = lambda today=None: L.FrozenFetcher(src)
    A._live_region.cache_clear()
    try:
        return run(question, si)
    finally:
        A.USE_LIVE, L.FETCHER_FACTORY, L.LIVE_DIR = old
        A._live_region.cache_clear()


def predict(c: dict, split: str, si: dict, region: dict) -> dict:
    base = {"id": c["id"], "split": split, "node": "A", "tags": c.get("tags", []), "point": c.get("point", ""),
            "expected": c["expect"], "region_source": region.get("source", "snapshot")}
    try:
        out = run_case(c["question"], si, region)
    except FileNotFoundError as e:
        return {**base, "status": "skipped", "reason": str(e)}
    ar = out.get("analysis_result") or {}
    fit = next((t for t in out["trace"] if t["node"] == "A.check_fitness"), {})
    m = ar.get("metrics", {})
    pred = {"fitness": fit.get("fitness"), "cases": sorted(fit.get("cases") or []), "mode": ar.get("mode"),
            "diagnosis": ar.get("diagnosis"), "evidence_strength": ar.get("evidence_strength")}
    exp = c["expect"]
    checks = {"diagnosis": lambda: pred["diagnosis"] == exp["diagnosis"],
              "fitness": lambda: pred["fitness"] == exp["fitness"] and pred["cases"] == sorted(exp.get("cases", [])),
              "mode": lambda: pred["mode"] == exp["mode"]}
    correct = {k: f() for k, f in checks.items() if k in exp}      # 정답에 있는 항목만 채점(개발 사례는 진단만)
    return {**base, "status": out.get("status"), "predicted": pred, "correct": correct, "all_correct": all(correct.values()),
            "snapshot_id": ar.get("snapshot_id"), "verify_passed": (out.get("verify_result") or {}).get("passed"),
            "observed": {"region_prev_pct": m.get("region_visit_prev_pct"), "region_yoy_pct": m.get("region_visit_yoy_pct"),
                         "my_orders_pct": m.get("my_orders_change_pct")}}


def dev_cases() -> list[tuple[dict, dict]]:
    """S9 개발 사례(합성 4유형). 기대 유형이 지역 숫자상 나올 수 없으면 evaluable=False."""
    out = []
    for s in json.loads((MOCK_DIR / "synthetic_stores_asof_20260831.json").read_text(encoding="utf-8")):
        si = s["store_input"]
        out.append(({"id": s["case_id"], "question": f"{si.get('dong_name')}에서 카페 해요. 손님이 줄었어요",
                     "expect": {"diagnosis": s["expected_diagnosis"]}, "tags": ["dev"]}, si))
    return out


def metrics_of(preds: list[dict]) -> dict:
    ev = [p for p in preds if p["split"] == "eval" and p["status"] != "skipped"]
    dev = [p for p in preds if p["split"] == "dev" and p.get("evaluable")]
    n = len(ev)
    acc = lambda key: round(sum(p["correct"][key] for p in ev) / n, 4) if n else None  # noqa: E731
    per_tag = defaultdict(lambda: [0, 0])
    for p in ev:
        for t in p["tags"]:
            per_tag[t][0] += p["all_correct"]
            per_tag[t][1] += 1
    confusion = Counter(f"{p['expected']['diagnosis']}→{p['predicted']['diagnosis']}" for p in ev)
    return {
        "eval_n": n, "eval_skipped": sum(p["status"] == "skipped" for p in preds if p["split"] == "eval"),
        "eval_fitness_accuracy": acc("fitness"), "eval_diagnosis_accuracy": acc("diagnosis"), "eval_mode_accuracy": acc("mode"),
        "eval_all_correct_rate": round(sum(p["all_correct"] for p in ev) / n, 4) if n else None,
        "eval_verify_pass_rate": round(sum(bool(p["verify_passed"]) for p in ev) / n, 4) if n else None,
        "dev_evaluable_n": len(dev), "dev_not_evaluable_n": sum(p["split"] == "dev" and not p.get("evaluable") for p in preds),
        "dev_diagnosis_accuracy": round(sum(p["correct"]["diagnosis"] for p in dev) / len(dev), 4) if dev else None,
        "per_tag": {t: f"{a}/{b}" for t, (a, b) in sorted(per_tag.items())},
        "diagnosis_confusion": dict(sorted(confusion.items())),
        "note": "합성 사례 기준. 실제 점주 대상 정확도 아님",
    }


def main() -> None:
    preds = []
    for line in (ROOT / "eval" / "cases_a.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            c = json.loads(line)
            preds.append(predict(c, "eval", build_store(c["store"]), c.get("region", {})))
    for c, si in dev_cases():
        p = predict(c, "dev", si, {"source": "snapshot"})
        o = p["observed"]
        reach = A.reachable_diagnoses(o["region_prev_pct"], o["region_yoy_pct"], 0.0) if o["region_prev_pct"] is not None else set()
        p["evaluable"] = c["expect"]["diagnosis"] in reach or c["expect"]["diagnosis"] == "undetermined"
        p["correct"] = {"diagnosis": p["predicted"]["diagnosis"] == c["expect"]["diagnosis"]}
        p["all_correct"] = p["correct"]["diagnosis"]
        preds.append(p)

    met = metrics_of(preds)
    print("■ A 평가 (합성 사례 기준, 실제 점주 대상 정확도 아님)")
    print("| id | 태그 | 기대 판정·진단 | 예측 판정·진단 | 결과 |")
    print("|---|---|---|---|---|")
    for p in preds:
        e = p["expected"]
        if p["status"] == "skipped":
            print(f"| {p['id']} | {','.join(p['tags'])} | {e.get('fitness', '-')} {e.get('cases', '')} {e['diagnosis']} | - | 건너뜀: {p['reason']} |")
            continue
        q = p["predicted"]
        mark = ("✅" if p["all_correct"] else "❌") if p["split"] == "eval" or p.get("evaluable") else "평가 불가"
        print(f"| {p['id']} | {','.join(p['tags'])} | {e.get('fitness', '-')} {e.get('cases', '')} {e['diagnosis']} | "
              f"{q['fitness']} {q['cases']} {q['diagnosis']} | {mark} |")
    print(f"\n평가 문항 {met['eval_n']}개(건너뜀 {met['eval_skipped']}): 적합성 판정 {met['eval_fitness_accuracy']} · "
          f"진단 {met['eval_diagnosis_accuracy']} · 모드 {met['eval_mode_accuracy']} · 검증 통과 {met['eval_verify_pass_rate']}")
    print(f"개발 사례: 진단 {met['dev_diagnosis_accuracy']} (평가 가능 {met['dev_evaluable_n']}개, 평가 불가 {met['dev_not_evaluable_n']}개)")
    d = runlog.write_run("A", preds, met, [p["snapshot_id"] for p in preds if p.get("snapshot_id")])
    print(f"→ 실행 기록 {d.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
