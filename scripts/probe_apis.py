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
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "raw" / "samples"
BASE = "https://open.jejudatahub.net/api/proxy"
# 데이터셋 번호 → API id (DATA_INTAKE.md)
API = {
    753: "Daaa1t3at3tt8a8DD3t55538t35Dab1t",  # 읍면동 내국인 유동인구
    748: "t18b1bttt8abaDt51b8aat85ttaa8t5b",  # 읍면동 외국인 단기체류 유동인구
    756: "1aD5taat1attaa51Db1511b51ab9Da19",  # 일별 기상
    757: "t1bab75a555a15715btaa7a5abaa1aab",  # 기상관측소
}

load_dotenv(ROOT / ".env")
KEY = os.getenv("JEJU_DATAHUB_KEY", "").strip()
GO_KEY = os.getenv("DATA_GO_KR_API_KEY", "").strip()   # 공공데이터포털(기상청 ASOS 등)
ASOS_URL = "https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList"
ASOS_JEJU = {"184": "제주", "185": "고산", "188": "성산", "189": "서귀포"}  # 제주도 종관 관측소


def _redact(text: str) -> str:
    from urllib.parse import quote
    for k in (KEY, GO_KEY):
        if k:  # 원문·URL 인코딩된 형태 둘 다 가림
            text = text.replace(k, "***").replace(quote(k, safe=""), "***")
    return text


RETRIES = 6          # 서버가 같은 요청에도 무작위로 502를 냄(S1에서 확인) → 재시도 필수
STATS = {"calls": 0, "retries": 0}


def _offline_guard() -> None:
    """tasks.py reproduce/test/eval/demo 는 COACH_OFFLINE=1 로 실행 → API 호출이 끼어들면 즉시 실패."""
    if os.getenv("COACH_OFFLINE") == "1":
        raise RuntimeError("COACH_OFFLINE=1: 재현 단계에서는 API를 부르지 않습니다(스냅샷만 읽기). "
                           "데이터가 필요하면 python tasks.py snapshot")


def call(no: int, **params) -> dict:
    """API 호출 → JSON dict. 5xx·네트워크 오류는 재시도, 끝내 실패하면 키를 가린 메시지로 RuntimeError."""
    _offline_guard()
    if not KEY:
        raise RuntimeError(".env 에 JEJU_DATAHUB_KEY 가 없음")
    url = f"{BASE}/{API[no]}/{KEY}"
    last = ""
    for attempt in range(RETRIES):
        STATS["calls"] += 1
        body = ""
        try:
            r = requests.get(url, params=params, timeout=30)
            body = r.text
            if r.status_code >= 500:   # 무작위 502 → 잠시 후 다시
                raise requests.HTTPError(f"{r.status_code} {r.reason}")
            r.raise_for_status()       # 4xx 는 재시도해도 같으므로 바로 실패
            return r.json()
        except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as e:
            last = f"{type(e).__name__}: {e} | body={body[:300]}"
            if isinstance(e, requests.HTTPError) and body and not str(e).startswith("5"):
                break
            STATS["retries"] += 1
            time.sleep(min(0.5 * 2 ** attempt, 8))
        except ValueError as e:        # JSON 이 아닌 응답
            last = f"JSON 아님: {e} | body={body[:300]}"
            break
    raise RuntimeError(_redact(f"{no} 호출 실패({params}): {last}"))


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


def call_asos(start: str, end: str, stn: str, rows: int = 400) -> dict:
    """기상청 지상(종관, ASOS) 일자료. 날짜 YYYYMMDD, 종료일은 '어제'까지만 가능(기상청 규칙)."""
    _offline_guard()
    if not GO_KEY:
        raise RuntimeError(".env 에 DATA_GO_KR_API_KEY 가 없음")
    from urllib.parse import unquote
    params = {"serviceKey": unquote(GO_KEY),  # 'Encoding 키'를 넣었어도 requests 가 한 번만 인코딩하게
              "pageNo": 1, "numOfRows": rows, "dataType": "JSON", "dataCd": "ASOS", "dateCd": "DAY",
              "startDt": start, "endDt": end, "stnIds": stn}
    last = ""
    for attempt in range(RETRIES):
        STATS["calls"] += 1
        body = ""
        try:
            r = requests.get(ASOS_URL, params=params, timeout=30)
            body = r.text
            r.raise_for_status()
            j = r.json()
            code = j["response"]["header"]["resultCode"]
            if code != "00":
                raise RuntimeError(f"resultCode={code} {j['response']['header'].get('resultMsg')}")
            return j
        except (requests.RequestException, ValueError, KeyError, RuntimeError) as e:
            last = f"{type(e).__name__}: {e} | body={body[:300]}"
            if isinstance(e, (ValueError, KeyError, RuntimeError)):  # 형식·권한 오류는 재시도해도 같음
                break
            STATS["retries"] += 1
            time.sleep(min(0.5 * 2 ** attempt, 8))
    raise RuntimeError(_redact(f"ASOS 호출 실패({start}~{end}, stn={stn}): {last}"))


def asos_items(j: dict) -> list[dict]:
    body = j["response"]["body"]
    items = (body.get("items") or {}).get("item") or []
    return items if isinstance(items, list) else [items]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    cmd, *rest = sys.argv[1:] or ["sample"]
    {"sample": cmd_sample, "latest": cmd_latest, "raw": cmd_raw}[cmd](*rest)
