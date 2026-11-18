"""★A 데이터 적합성 판정 — "이 질문에 지금 데이터로 답해도 되는가"를 결정론적 규칙으로 판정한다(LLM 아님).

판정: 적합 / 범위 축소 / 판정 불가 (계약 변경 없이 mode + notes 접두어 + trace 로 표현)
| 사례 | 상황                                         | 판정       | 결과 |
| F1  | 카페·시범 읍면동·주문 12주+·기간 겹침·데이터 최신 | 적합       | mode=current |
| F2  | 질문에 카페가 아닌 업종 단어만 있음             | 범위 축소   | 동네 흐름만(내 가게 진단·실험 없음) |
| F3  | 주문 기록 12주 미만(0주 포함)                 | 범위 축소   | 동네 흐름만 |
| F4  | 시범 밖 읍면동 / 지역 코드 없음                | 판정 불가   | 지역 지표 없음 |
| F5  | 753 last_obs_date 가 기준일보다 7일 넘게 이름   | 범위 축소   | 그 이후 변화는 판단 불가 안내(진단은 유지) |
| F6  | 748 빠진 칸·0값 비율 > 10%                   | 범위 축소   | 외국인 보조 지표(E05·E06) 빼고 내국인 기준만 |
| F7  | 주문 최근 6주 ↔ 지역 최근 창 겹침 < 21일        | 범위 축소   | 동네 흐름만 |
기준값 근거: 노션 decisions 2026-11-17 (①, ②, ③).

* 사유(notes)는 C 리포트 '한계'에 근거 번호 없이 실리므로, "숫자+%" 문장은 C 검증(missing_evidence)에 걸린다.
  정확한 비율은 Fitness.details → trace 로 남긴다(라이브 실행 2026-11-17 에서 발견).

라이브 모드 확장: 기간(region["window"])과 기준일(as_of)을 인자로 받으므로, 점주 기간에 맞춰 API 로 받은 지역 지표를
넣어도 같은 판정을 쓴다. 그때 F7 은 '축소' 대신 '그 기간 데이터를 가져오기'로 바뀐다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

STORE_WEEKS = 6                       # 동네 비교 창과 같은 길이(decisions ②-주수)
MIN_STORE_WEEKS = 2 * STORE_WEEKS     # 12주 미만이면 내 가게 비교 안 함 → 범위 축소
MIN_OVERLAP_DAYS = 21                 # 최근 6주(42일)의 절반
LAG_WARN_DAYS = 7                     # last_obs_date 가 기준일보다 이만큼 넘게 이르면 경고(decisions ①-3)
FOREIGN_GAP_MAX_RATIO = 0.10          # 748 빠진 칸+0값 비율 허용치(decisions ①-2)

FIT, NARROW, UNABLE = "적합", "범위 축소", "판정 불가"
# F2: 계약상 industry 는 'cafe' 고정이라 질문 글로만 알 수 있다. 카페 단어가 함께 있으면 카페로 본다.
CAFE_WORDS = ("카페", "커피", "디저트", "베이커리카페", "브런치")
OTHER_INDUSTRY_WORDS = ("고깃집", "식당", "음식점", "횟집", "치킨", "피자", "분식", "김밥", "국수", "중국집", "한식",
                        "일식", "술집", "주점", "호프", "펍", "편의점", "마트", "미용실", "네일", "옷가게", "의류",
                        "꽃집", "학원", "펜션", "게스트하우스", "민박", "숙박", "렌터카")


@dataclass
class Fitness:
    verdict: str = FIT
    cases: list[str] = field(default_factory=list)   # 걸린 사례 번호 (F1 = 아무것도 안 걸림)
    notes: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)       # 사유의 정확한 수치(trace 용). 사유 문장에는 % 수치를 넣지 않는다*
    store_compare: bool = True     # False → 내 가게 비교(진단) 안 함, 동네 흐름만
    use_foreign: bool = True       # False → 외국인 보조 지표(E05·E06) 표시 안 함
    has_region: bool = True

    @property
    def mode(self) -> str:
        return "current" if (self.store_compare and self.has_region) else "region_only"

    def add(self, case: str, verdict: str, note: str) -> None:
        self.cases.append(case)
        self.notes.append(f"[{verdict}] {note}")
        if verdict == UNABLE or (verdict == NARROW and self.verdict == FIT):
            self.verdict = verdict


def parse_window(label: str) -> tuple[date, date] | None:
    """'2026-07-20~2026-08-30' → (시작, 끝). mock 처럼 날짜가 아닌 라벨이면 None."""
    try:
        a, b = label.split("~")
        return date.fromisoformat(a.strip()), date.fromisoformat(b.strip())
    except (ValueError, AttributeError):
        return None


def store_recent_span(weeks: list[dict]) -> tuple[date, date] | None:
    """주문 기록 중 비교에 쓰는 최근 6주의 (첫날, 마지막 날)."""
    w = sorted(weeks, key=lambda x: x["week_start"])[-STORE_WEEKS:]
    if not w:
        return None
    first = date.fromisoformat(str(w[0]["week_start"]))
    last = date.fromisoformat(str(w[-1]["week_start"])) + timedelta(days=6)
    return first, last


def overlap_days(a: tuple[date, date], b: tuple[date, date]) -> int:
    return max(0, (min(a[1], b[1]) - max(a[0], b[0])).days + 1)


def industry_word(question: str) -> str | None:
    """카페가 아닌 업종 단어(카페 단어가 함께 있으면 None)."""
    q = question or ""
    if any(w in q for w in CAFE_WORDS):
        return None
    return next((w for w in OTHER_INDUSTRY_WORDS if w in q), None)


def judge(store_input: dict, question: str, region: dict, as_of: date | None = None) -> Fitness:
    """region = _region() 결과(모양 고정). as_of 가 None 이면(mock) F5 를 건너뛴다. 날짜 없는 창이면 F7 을 건너뛴다."""
    f = Fitness()
    code, name = store_input.get("dong_code"), store_input.get("dong_name") or store_input.get("dong_code")
    weeks = store_input.get("weeks") or []
    dong = region.get("dongs", {}).get(code or "")
    pilot = "·".join(d.get("dong_name", c) for c, d in region.get("dongs", {}).items())

    # F4 판정 불가: 지역 지표가 없으면 동네 흐름도 줄 수 없다
    if dong is None:
        f.has_region = False
        f.add("F4", UNABLE, f"{name or '읍면동 미지정'}: 해당 읍면동 스냅샷 없음(시범 지역: {pilot}) → 지역 지표로 진단할 수 없음")

    # F2 범위 축소: 카페가 아닌 업종
    if (w := industry_word(question)) is not None:
        f.store_compare = False
        f.add("F2", NARROW, f"질문에 카페가 아닌 업종('{w}')이 보여요 → MVP는 카페 기준 실험만 있어 동네 흐름만 제공하고 "
                            f"내 가게 진단·실험 추천은 하지 않음")

    # F3 범위 축소: 주문 기록 부족
    if len(weeks) < MIN_STORE_WEEKS:
        f.store_compare = False
        have = f"{len(weeks)}주" if weeks else "없음"
        f.add("F3", NARROW, f"주문 기록 {have} → 최근 6주와 직전 6주를 비교하려면 {MIN_STORE_WEEKS}주가 필요해 동네 흐름만 제공")

    # F7 범위 축소: 주문 기록 최근 6주와 지역 최근 창이 충분히 겹치지 않음 (실데이터처럼 창에 날짜가 있을 때만)
    recent = parse_window(region.get("window", {}).get("recent", ""))
    span = store_recent_span(weeks) if len(weeks) >= MIN_STORE_WEEKS else None
    if dong is not None and recent and span:
        ov = overlap_days(span, recent)
        if ov < MIN_OVERLAP_DAYS:
            f.store_compare = False
            f.add("F7", NARROW, f"주문 기록 최근 6주({span[0]}~{span[1]})와 지역 데이터 최근 창({recent[0]}~{recent[1]})이 "
                                f"{ov}일만 겹침(기준 {MIN_OVERLAP_DAYS}일) → 같은 기간 비교가 어려워 동네 흐름만 제공")

    # F5 범위 축소: 지역 데이터가 기준일보다 오래됨 (진단은 유지하되 안내)
    last753 = next((x.get("last_obs_date") for x in region.get("freshness", []) if x.get("dataset_no") == 753), None)
    if as_of and last753:
        lag = (as_of - date.fromisoformat(str(last753))).days
        if lag > LAG_WARN_DAYS:
            f.add("F5", NARROW, f"지역 데이터는 {last753}까지 → 그 이후(기준일 {as_of}까지 {lag}일) 변화는 판단 불가")

    # F6 범위 축소: 외국인 값 빠진 칸이 많음 → 보조 지표만 뺌(진단은 원래 내국인 기준)
    gap = ((dong or {}).get("_quality") or {}).get("foreign_gap_ratio")
    if gap is not None and gap > FOREIGN_GAP_MAX_RATIO:
        f.use_foreign = False
        f.details["foreign_gap_ratio"] = gap
        f.add("F6", NARROW, f"{dong.get('dong_name')} 외국인 단기체류 값에 빠지거나 0인 칸이 허용 기준보다 많음 → 외국인 흐름은 "
                            f"표시하지 않고 내국인 방문 기준으로만 진단")

    if not f.cases:
        f.cases.append("F1")
    return f
