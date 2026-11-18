"""S1 데이터 조사용: 제주데이터허브 API를 소량 호출해 응답 샘플을 data/raw/samples/ 에 저장한다.

사용:
  python scripts/probe_apis.py sample            # 4개 API 각 1회 호출 → 샘플 저장
  python scripts/probe_apis.py latest            # 753·748·756의 실제 마지막 관측일 탐색
  python scripts/probe_apis.py raw 753 startDate=20251101 endDate=20251101 emd=애월읍

보안: 키는 .env(JEJU_DATAHUB_KEY)에서만 읽고, 화면 출력·저장 파일 어디에도 남기지 않는다
(키가 URL 경로에 들어가므로 오류 메시지까지 가린다).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# API 호출·키 가리기·재시도·오프라인 차단은 에이전트 본체와 같은 코드를 쓴다(src/coach/data/api_client.py)
from coach.data.api_client import (API, ASOS_JEJU, GO_KEY, KEY, ROOT, STATS, asos_items,  # noqa: E402,F401
                                   call, call_asos)

SAMPLE_DIR = ROOT / "data" / "raw" / "samples"


def save(name: str, no: int, params: dict, resp: dict) -> Path:
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    p = SAMPLE_DIR / f"{name}.json"
    p.write_text(json.dumps({"dataset_no": no, "params": params, "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                             "response": resp}, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def summary(resp: dict) -> str:
    data = resp.get("data") or []
    cols = list(data[0].keys()) if data else []
    return f"totCnt={resp.get('totCnt')} hasMore={resp.get('hasMore')} rows={len(data)} cols={cols}"


def cmd_sample():
    d = "20251101"
    jobs = [
        ("753_aewol_1day", 753, {"startDate": d, "endDate": d, "emd": "애월읍", "limit": "100"}),
        ("748_aewol_1day", 748, {"startDate": d, "endDate": d, "emd": "애월읍", "limit": "100"}),
        ("756_all_1day", 756, {"searchDate": d, "limit": "100"}),
        ("757_all", 757, {"limit": "100"}),
    ]
    for name, no, params in jobs:
        try:
            resp = call(no, **params)
            print(f"[{no}] {name}: {summary(resp)} -> {save(name, no, params, resp).relative_to(ROOT)}")
        except RuntimeError as e:
            print(f"[{no}] {name}: {e}")


def _has_data(no: int, day: date, extra: dict) -> bool:
    s = day.strftime("%Y%m%d")
    params = {"searchDate": s} if no == 756 else {"startDate": s, "endDate": s}
    resp = call(no, **params, **extra)
    return bool(resp.get("data"))


def cmd_latest():
    """하루 단위 이진 탐색으로 '데이터가 있는 마지막 날'을 찾는다(데이터가 중간에 끊기지 않는다고 가정)."""
    today = date.today()
    targets = [(753, {"emd": "애월읍"}, date(2017, 1, 1)), (748, {"emd": "애월읍"}, date(2018, 1, 1)),
               (756, {}, date(2015, 1, 1))]
    for no, extra, lo in targets:
        if _has_data(no, today, extra):
            print(f"[{no}] 오늘({today})도 데이터 있음")
            continue
        if not _has_data(no, lo, extra):
            print(f"[{no}] 시작일 {lo} 에도 데이터 없음 → 파라미터 확인 필요")
            continue
        hi, calls = today, 2  # lo: 있음, hi: 없음
        while (hi - lo).days > 1:
            mid = lo + timedelta(days=(hi - lo).days // 2)
            calls += 1
            if _has_data(no, mid, extra):
                lo = mid
            else:
                hi = mid
        print(f"[{no}] 마지막 관측일 = {lo}  (호출 {calls}회, 조건 {extra or '전체'})")


def cmd_raw(no: str, *kv: str):
    params = dict(x.split("=", 1) for x in kv)
    resp = call(int(no), **params)
    print(summary(resp))
    print(json.dumps((resp.get("data") or [])[:3], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    cmd, *rest = sys.argv[1:] or ["sample"]
    {"sample": cmd_sample, "latest": cmd_latest, "raw": cmd_raw}[cmd](*rest)
