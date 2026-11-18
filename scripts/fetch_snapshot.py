"""S2-1: 스냅샷 원본 내려받기 → data/raw/fetch/<as_of>/ 에 API 응답(JSON) 그대로 저장.

- 받은 페이지는 파일로 남기므로 중간에 끊겨도 다시 실행하면 이어서 받는다.
- 실행 중 분석 코드는 API를 부르지 않는다. 이 스크립트만 API를 부른다(재현성).
- 기간은 노션 A파트 decisions 의 비교 창을 모두 포함하는 연속 구간(전년 동기 시작 ~ 기준일).

사용: python tasks.py snapshot --as-of 2026-08-31   (또는 COACH_AS_OF=2026-08-31 python scripts/fetch_snapshot.py)
키가 필요한 유일한 단계다(.env 의 JEJU_DATAHUB_KEY, DATA_GO_KR_API_KEY).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import probe_apis as api  # noqa: E402  (키 처리·재시도·가리기는 probe_apis 에 한 곳만)
from coach.data import region_real as R  # noqa: E402  (기준일·창·시범 읍면동은 region_real 한 곳에서)

AS_OF = R.AS_OF.strftime("%Y%m%d")
START = R.windows()["yoy"].start.strftime("%Y%m%d")   # 전년 동기 창 시작 ~ 기준일 (연속 구간)
DONGS = list(R.STATION_OF)                             # 시범 읍면동 (시나리오는 노형동, 나머지는 비교용)
ASOS_STN = sorted(set(R.STATION_OF.values()))          # 가장 가까운 관측소 (노션 decisions 6-1)
OUT = api.ROOT / "data" / "raw" / "fetch" / AS_OF
api.RETRIES = 10


def _save(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)  # 다 쓴 뒤에 이름 바꾸기 → 반쯤 쓴 파일이 남지 않음


def fetch_hub(no: int, emd: str) -> int:
    """제주데이터허브 753/748: 기간 조회 + 페이지(number) 넘기기."""
    page, rows = 1, 0
    while True:
        p = OUT / f"{no}" / emd / f"p{page:04d}.json"
        if p.exists():
            resp = json.loads(p.read_text(encoding="utf-8"))
        else:
            params = {"startDate": START, "endDate": AS_OF, "emd": emd, "limit": "100", "number": str(page)}
            resp = api.call(no, **params)
            _save(p, {"dataset_no": no, "params": params, "response": resp})
            resp = {"response": resp}
        r = resp["response"]
        rows += len(r.get("data") or [])
        if page % 20 == 0:
            print(f"  [{no}] {emd} p{page} 누적 {rows}/{r.get('totCnt')}", flush=True)
        if not r.get("hasMore"):
            return rows
        page += 1


def fetch_asos(stn: str) -> int:
    p = OUT / "asos" / f"{stn}.json"
    if not p.exists():
        j = api.call_asos(START, AS_OF, stn, rows=999)
        _save(p, {"dataset": "kma_asos_daily", "params": {"start": START, "end": AS_OF, "stn": stn}, "response": j})
    j = json.loads(p.read_text(encoding="utf-8"))["response"]
    return len(api.asos_items(j))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"기간 {START}~{AS_OF} · 읍면동 {DONGS} · ASOS {ASOS_STN} → {OUT.relative_to(api.ROOT)}", flush=True)
    for stn in ASOS_STN:
        print(f"[ASOS] {stn} {api.ASOS_JEJU[stn]}: {fetch_asos(stn)}일", flush=True)
    for no in (753, 748):
        for emd in DONGS:
            print(f"[{no}] {emd}: {fetch_hub(no, emd)}행 완료", flush=True)
    print("호출 통계:", api.STATS)
