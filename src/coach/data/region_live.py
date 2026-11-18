"""★A 라이브 모드 (COACH_MOCK=0 COACH_LIVE=1) — 점주 주문 기록 시기에 맞춰 동네 흐름을 API로 불러와 분석한다.

흐름 (계산은 스냅샷 모드와 같은 순수 함수 region_real.dong_metrics 를 쓴다)
  1. 기준일 = 주문 기록 마지막 주의 일요일 (기록이 없으면 어제)
  2. 원천의 실제 마지막 관측일 확인(이진 탐색, 하루 캐시) → 동네 창은 min(기준일, 마지막 관측일) 기준
     → 기준일이 더 늦으면 적합성 판정 F5(지연)·F7(기간 불일치)이 그대로 안내한다
  3. 그 읍면동·그 기간만 조회(페이지 캐시: 같은 요청은 즉시)
  4. 조회한 표를 data/live/snapshots/<읍면동>_<창 끝>/ 에 parquet + manifest 로 '얼려' 저장 → 라이브 응답도 재현 가능
  5. 조회 실패(서버 502·키 없음·오프라인)면 기준일 스냅샷으로 대체하고 사유를 남긴다(region_for)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from . import api_client as api
from . import region_real as R
from .snapshot_io import domestic_df, foreign_df, weather_df, write_snapshot

LIVE_DIR = R.ROOT / "data" / "live"
KNOWN_GOOD = date(2026, 8, 31)     # 753·748 데이터가 있다고 확인된 날(S1·S2) → 마지막 관측일 탐색의 하한


def target_date(store_input: dict, today: date | None = None) -> date:
    """기준일: 주문 기록 마지막 주의 일요일. 기록이 없으면 어제(오늘 데이터는 아직 없음)."""
    weeks = store_input.get("weeks") or []
    if weeks:
        last = max(date.fromisoformat(str(w["week_start"])) for w in weeks)
        return last + timedelta(days=6)
    return (today or date.today()) - timedelta(days=1)


@dataclass
class ApiFetcher:
    """실제 API 조회 + 캐시. 테스트에서는 같은 메서드를 가진 가짜로 갈아 끼운다."""
    root: Path = LIVE_DIR / "cache"
    today: date | None = None

    def latest(self, no: int, emd: str, upper: date) -> date:
        """upper 까지 중 데이터가 있는 마지막 날. 같은 날 같은 질문은 캐시."""
        today = str(self.today or date.today())
        f = self.root / "latest.json"
        cache = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        k = f"{no}|{emd}|{upper}"
        if cache.get(k, {}).get("checked_on") == today:
            return date.fromisoformat(cache[k]["last"])
        last = api.latest_obs(no, emd, KNOWN_GOOD, upper)
        cache[k] = {"checked_on": today, "last": str(last)}
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        return last

    def hub(self, no: int, emd: str, start: date, end: date) -> list[dict]:
        s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        return api.hub_rows(no, emd, s, e, self.root / str(no) / emd / f"{s}_{e}")

    def asos(self, stn: str, start: date, end: date) -> list[dict]:
        s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        return api.asos_rows(stn, s, e, self.root / "asos" / f"{stn}_{s}_{e}.json")


class FrozenFetcher:
    """동결된 스냅샷 폴더(라이브 조회 결과 또는 기준일 스냅샷)를 'API 응답 모양'으로 다시 꺼내 주는 재생용 fetcher.
    평가·재현에서 라이브 사례를 네트워크 없이 같은 숫자로 다시 돌릴 때 쓴다. 폴더는 manifest 로 무결성 검사."""

    def __init__(self, snapshot_dir: Path, last_obs: date | None = None):
        man, errs = R.manifest_errors(Path(snapshot_dir))
        if errs:
            raise ValueError("; ".join(errs))
        import pandas as pd
        self.pd = pd
        d = Path(snapshot_dir)
        self.dom = pd.read_parquet(d / "domestic_753.parquet")
        self.fo = pd.read_parquet(d / "foreign_748.parquet")
        self.wt = pd.read_parquet(d / "weather_asos.parquet")
        self.last = last_obs or date.fromisoformat(man.get("data_last_obs") or man["files"]["domestic_753.parquet"]["last_date"])

    def latest(self, no, emd, upper):
        return min(upper, self.last)

    def hub(self, no, emd, start, end):
        df = self.dom if no == 753 else self.fo
        df = df[(df["emd"] == emd) & (df["base_date"] >= start) & (df["base_date"] <= end)]
        ren = ({"age_group": "ageGroup", "resd_pop": "resdPop", "work_pop": "workPop", "visit_pop": "visitPop"}
               if no == 753 else {"visit_pop": "visitPop"})
        out = df.rename(columns=ren).assign(baseDate=df["base_date"].map(lambda d: d.strftime("%Y%m%d")))
        return out.drop(columns=["base_date"]).to_dict("records")

    def asos(self, stn, start, end):
        w = self.wt[(self.wt["stn_id"] == stn) & (self.wt["base_date"] >= start) & (self.wt["base_date"] <= end)]
        f = lambda v: "" if self.pd.isna(v) else f"{v}"  # noqa: E731  (기상청은 빈 강수량을 "" 로 줌)
        return [{"tm": str(r.base_date), "stnId": r.stn_id, "stnNm": r.stn_name, "sumRn": f(r.sum_rn_mm),
                 "avgTa": f(r.avg_ta_c), "minTa": f(r.min_ta_c), "maxTa": f(r.max_ta_c)} for r in w.itertuples()]


# fetcher 를 지정하지 않으면 이걸로 만든다(평가·재현에서 FrozenFetcher 로 바꿔 끼움)
FETCHER_FACTORY: Callable[..., object] = lambda today=None: ApiFetcher(today=today)  # noqa: E731


def build_region_live(dong_codes: dict[str, str], dong_code: str | None, target: date, fetcher=None,
                      today: date | None = None, out_root: Path | None = None) -> dict:
    """_region() 과 같은 모양 + as_of·live 키. 시범 밖이면 dongs 가 비어 있다(→ F4)."""
    fetcher = fetcher or FETCHER_FACTORY(today=today)
    name = next((n for n, c in dong_codes.items() if c == dong_code), None)
    base = {"snapshot_id": "live-none", "window": {"recent": "", "prev": "", "yoy": ""}, "dongs": {},
            "freshness": [], "as_of": str(target), "excluded": [], "live": {"target": str(target)}}
    if name not in R.STATION_OF:
        return base

    last = fetcher.latest(753, name, target)
    ws = R.windows(R.last_sunday(min(target, last)))
    main, yoy = (ws["prev"].start, ws["recent"].end), (ws["yoy"].start, ws["yoy"].end)
    stn = R.STATION_OF[name]
    asos_end = min(ws["recent"].end, (today or date.today()) - timedelta(days=1))   # 기상청은 어제까지만
    tables = {
        "domestic": domestic_df(fetcher.hub(753, name, *main) + fetcher.hub(753, name, *yoy)),
        "foreign": foreign_df(fetcher.hub(748, name, *main) + fetcher.hub(748, name, *yoy)),
        "weather": weather_df(fetcher.asos(stn, ws["yoy"].start, asos_end)),
    }
    labels = {k: w.label() for k, w in ws.items()}
    out = (out_root or LIVE_DIR / "snapshots") / f"{name}_{ws['recent'].end}"
    man = write_snapshot(out, tables, as_of=str(target),
                         extra={"mode": "live", "emd": name, "windows": labels, "data_last_obs": str(last)})
    m = R.dong_metrics({"manifest": man, **tables}, name, ws)

    fetched = str(today or date.today())
    region = {
        **base, "snapshot_id": man["snapshot_id"], "window": labels,
        "freshness": [
            {"dataset_no": 753, "name": "읍면동 단위 내국인 유동인구", "last_obs_date": str(last), "unit": "읍면동/일",
             "used": True, "reason": f"진단 지표(내국인 방문). 실시간 조회(조회일 {fetched}), 원천 마지막 관측 {last}"},
            {"dataset_no": 748, "name": "읍면동 외국인 단기체류 유동인구",
             "last_obs_date": man["files"]["foreign_748.parquet"]["last_date"], "unit": "읍면동/일", "used": True,
             "reason": "보조 지표(외국인 단기체류 변화율), 실시간 조회. 753과 합산하지 않음"},
            {"dataset_no": 15059093, "name": "기상청 지상(종관, ASOS) 일자료",
             "last_obs_date": man["files"]["weather_asos.parquet"]["last_date"], "unit": "관측소/일", "used": True,
             "reason": f"강수일(≥{R.RAIN_MM:g}mm) 판정, 가장 가까운 관측소({stn}). 실시간 조회"},
            *({"dataset_no": no, **v} for no, v in R.SURVEY.items()),
        ],
        "live": {"target": str(target), "data_last_obs": str(last), "fetched_on": fetched,
                 "snapshot_dir": str(out.relative_to(R.ROOT)) if out.is_relative_to(R.ROOT) else str(out)},
    }
    if m["_quality"]["fit"]:
        region["dongs"] = {dong_code: m}
    else:
        region["excluded"] = [f"[범위 축소] {name}: 조회한 기간에 빠진 날이 많음(결측 {m['_quality']['missing_ratio']}) "
                              f"→ 지역 지표를 쓰지 않음"]
    return region


def region_for(store_input: dict, dong_codes: dict[str, str], snapshot_region: Callable[[], dict],
               fetcher=None, today: date | None = None, out_root: Path | None = None) -> dict:
    """라이브 조회, 실패하면 기준일 스냅샷으로 대체(사유를 excluded 에 남김 → check_fitness 가 notes 로 보여 줌)."""
    target = target_date(store_input, today)
    try:
        return build_region_live(dong_codes, store_input.get("dong_code"), target, fetcher, today, out_root)
    except Exception as e:  # 서버 오류·키 없음·오프라인·응답 형식 이상 모두 → 스냅샷 대체
        reg = dict(snapshot_region())       # 캐시된 dict 를 직접 고치지 않게 복사
        reason = str(e).split("|")[0][:120]
        reg["excluded"] = list(reg.get("excluded", [])) + [
            f"[범위 축소] 실시간 동네 데이터 조회 실패({type(e).__name__}: {reason}) → 기준일 {reg.get('as_of')} 스냅샷으로 분석"]
        reg["live"] = {"target": str(target), "fallback": True}
        return reg
