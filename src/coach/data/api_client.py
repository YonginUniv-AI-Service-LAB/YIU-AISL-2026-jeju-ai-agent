"""★A 외부 API 클라이언트 — 제주데이터허브(753·748 등) + 기상청 ASOS. 라이브 모드와 스냅샷 수집이 같이 쓴다.

보안: 키는 .env(JEJU_DATAHUB_KEY, DATA_GO_KR_API_KEY)에서만 읽고, 오류 메시지·로그·저장 파일에서 가린다
      (허브는 키가 URL 경로에 들어가므로 예외 메시지까지 가림).
안정성: 허브 서버는 같은 요청에도 무작위 502를 냄(S1) → 5xx·네트워크 오류는 지수 대기 재시도.
재현성: COACH_OFFLINE=1 이면 모든 호출을 거부(tasks.py reproduce·test·eval·demo).
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[3]
BASE = "https://open.jejudatahub.net/api/proxy"
API = {   # 데이터셋 번호 → API id
    753: "Daaa1t3at3tt8a8DD3t55538t35Dab1t",  # 읍면동 내국인 유동인구
    748: "t18b1bttt8abaDt51b8aat85ttaa8t5b",  # 읍면동 외국인 단기체류 유동인구
    756: "1aD5taat1attaa51Db1511b51ab9Da19",  # 일별 기상(2025-03-30 에서 멈춤)
    757: "t1bab75a555a15715btaa7a5abaa1aab",  # 기상관측소
}
ASOS_URL = "https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList"
ASOS_JEJU = {"184": "제주", "185": "고산", "188": "성산", "189": "서귀포"}

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
KEY = os.getenv("JEJU_DATAHUB_KEY", "").strip()
GO_KEY = os.getenv("DATA_GO_KR_API_KEY", "").strip()

RETRIES = 6
STATS = {"calls": 0, "retries": 0}


def _redact(text: str) -> str:
    from urllib.parse import quote
    for k in (KEY, GO_KEY):
        if k:  # 원문·URL 인코딩된 형태 둘 다 가림
            text = text.replace(k, "***").replace(quote(k, safe=""), "***")
    return text


def _offline_guard() -> None:
    if os.getenv("COACH_OFFLINE") == "1":
        raise RuntimeError("COACH_OFFLINE=1: 재현 단계에서는 API를 부르지 않습니다(스냅샷만 읽기). "
                           "데이터가 필요하면 python tasks.py snapshot")


def call(no: int, **params) -> dict:
    """허브 API 호출 → JSON dict. 5xx·네트워크 오류는 재시도, 끝내 실패하면 키를 가린 메시지로 RuntimeError."""
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


# ── 기간 조회 + 페이지 캐시 (스냅샷 수집·라이브 모드 공용)
def _save(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)  # 다 쓴 뒤에 이름 바꾸기 → 반쯤 쓴 파일이 남지 않음


def hub_rows(no: int, emd: str, start: str, end: str, cache_dir: Path, progress=None) -> list[dict]:
    """허브 753/748 기간 조회(YYYYMMDD) → 행 목록. 받은 페이지는 cache_dir 에 남겨 끊겨도 이어받고, 다시 부르면 즉시."""
    page, rows = 1, []
    while True:
        p = cache_dir / f"p{page:04d}.json"
        if p.exists():
            r = json.loads(p.read_text(encoding="utf-8"))["response"]
        else:
            params = {"startDate": start, "endDate": end, "emd": emd, "limit": "100", "number": str(page)}
            r = call(no, **params)
            _save(p, {"dataset_no": no, "params": params, "response": r})
        rows += r.get("data") or []
        if progress and page % 20 == 0:
            progress(f"[{no}] {emd} p{page} 누적 {len(rows)}/{r.get('totCnt')}")
        if not r.get("hasMore"):
            return rows
        page += 1


def asos_rows(stn: str, start: str, end: str, cache_file: Path) -> list[dict]:
    if not cache_file.exists():
        j = call_asos(start, end, stn, rows=999)
        _save(cache_file, {"dataset": "kma_asos_daily", "params": {"start": start, "end": end, "stn": stn}, "response": j})
    return asos_items(json.loads(cache_file.read_text(encoding="utf-8"))["response"])


def has_data(no: int, emd: str, day: date) -> bool:
    s = day.strftime("%Y%m%d")
    return bool(call(no, startDate=s, endDate=s, emd=emd, limit="1").get("data"))


def latest_obs(no: int, emd: str, known_good: date, upper: date) -> date:
    """known_good(데이터 있음 확인된 날) ~ upper 사이에서 '데이터가 있는 마지막 날'을 이진 탐색(약 log2(일수)회 호출).
    데이터가 중간에 끊기지 않는다고 가정(S1 에서 753·748 확인)."""
    if upper <= known_good:
        return upper
    if has_data(no, emd, upper):
        return upper
    lo, hi = known_good, upper
    while (hi - lo).days > 1:
        mid = lo + timedelta(days=(hi - lo).days // 2)
        lo, hi = (mid, hi) if has_data(no, emd, mid) else (lo, mid)
    return lo
