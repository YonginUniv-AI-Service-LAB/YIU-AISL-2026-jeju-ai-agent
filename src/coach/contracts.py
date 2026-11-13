"""팀 공통 데이터 계약(인터페이스). ★ 변경 시 3명 모두 합의 + CONTRACT_VERSION 올리기.

소유:  StoreInput(B) / AnalysisResult(A) / Report(C) / VerifyResult(C)
JSON Schema는 `python scripts/export_schemas.py` 로 schemas/*.json 재생성.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "0.1.0"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ───────────────────────── ① store_input (B → A, C)
class Changeable(str, Enum):
    menu = "menu"
    hours = "hours"
    price = "price"
    channel = "channel"  # 배달·테이크아웃·SNS 등


class WeeklyRecord(_Strict):
    week_start: date = Field(description="해당 주 월요일")
    sales_krw: int = Field(ge=0)
    orders: int = Field(ge=0)
    open_days: int = Field(ge=0, le=7)


class StoreInput(_Strict):
    dong_code: Optional[str] = Field(None, description="행정동/법정동 코드(10자리). A의 매핑표 기준")
    dong_name: Optional[str] = Field(None, description="예: 애월읍")
    industry: Literal["cafe"] = "cafe"  # MVP는 카페 고정
    weeks: list[WeeklyRecord] = Field(default_factory=list, description="최근 8~12주 권장. 비어 있으면 '지역 수요 참고 분석'으로 강등")
    open_hours: Optional[str] = Field(None, description="예: 09:00-20:00")
    staff_am: Optional[int] = Field(None, ge=0)
    staff_pm: Optional[int] = Field(None, ge=0)
    budget_krw: Optional[int] = Field(None, ge=0, description="이번 달 실험에 쓸 수 있는 예산")
    changeable: list[Changeable] = Field(default_factory=list)
    is_synthetic: bool = Field(False, description="데모용 합성 점주 데이터 여부(리포트에 표시)")


# ───────────────────────── ② analysis_result (A → C)
class Mode(str, Enum):
    current = "current"      # 일 갱신 데이터 + 점주 실적
    past_ref = "past_ref"    # 과거 참고(카드 ~2025.6 등)
    region_only = "region_only"  # 점주 실적 없음 → 지역 수요 참고만


class Diagnosis(str, Enum):
    co_decline = "co_decline"          # 동네 동반 하락
    only_me = "only_me"                # 나만 하락
    season_weather = "season_weather"  # 계절·날씨
    undetermined = "undetermined"      # 판단 불가


class Freshness(_Strict):
    dataset_no: int = Field(description="제주데이터허브 목록 No.")
    name: str
    last_obs_date: Optional[date] = None
    unit: str = Field(description="예: 읍면동/일")
    used: bool
    reason: str = Field(description="사용/제외 사유")


class Evidence(_Strict):
    claim_id: str = Field(pattern=r"^E\d{2,}$", description="예: E01. 리포트 문장과 1:1 연결")
    metric: str = Field(description="metrics 의 키")
    value: float
    unit: Literal["pct", "pp", "count", "krw", "ratio"]
    formula: str = Field(description="재계산 가능한 식(사람이 읽는 형태)")
    source_nos: list[int] = Field(description="사용한 데이터 No. 목록. 점주 입력은 0")
    period: str = Field(description="예: 2026-08-17~2026-11-08 vs 전년 동기")
    is_past_ref: bool = False


class AnalysisResult(_Strict):
    contract_version: str = CONTRACT_VERSION
    mode: Mode
    snapshot_id: str = Field(description="데이터 스냅샷 해시 앞 12자리(재현성)")
    data_freshness: list[Freshness]
    metrics: dict[str, Optional[float]] = Field(description="visit_yoy_pct, my_orders_yoy_pct, rain_effect_pp …")
    diagnosis: Diagnosis
    evidence_strength: Literal["강", "중", "약"]
    evidence: list[Evidence]
    notes: list[str] = Field(default_factory=list, description="범위 축소·제외 사유 등")


# ───────────────────────── ③ report (C → B → UI)
class Sentence(_Strict):
    text: str
    claim_ids: list[str] = Field(default_factory=list, description="근거 claim_id. 수치가 들어간 문장은 필수")


class Experiment(_Strict):
    catalog_id: str
    title: str
    duration_days: int = 14
    cost_krw: int = Field(ge=0)
    extra_staff: int = Field(0, ge=0)
    metric: str = Field(description="성공 판단 지표. 예: 평일 오후 공헌이익")
    stop_rule: str
    rationale: Sentence


class Report(_Strict):
    contract_version: str = CONTRACT_VERSION
    mode: Mode
    diagnosis: Diagnosis
    observed: list[Sentence]
    my_store: list[Sentence]
    hypotheses: list[Sentence]
    to_check: list[Sentence]
    experiment: Optional[Experiment] = None
    alternatives: list[Experiment] = Field(default_factory=list)
    support_programs: list[Sentence] = Field(default_factory=list)
    limits: list[Sentence]
    sources: list[str] = Field(description="데이터 No.·이름·기준일")
    is_synthetic_input: bool = False


# ───────────────────────── ④ verify_result (C → B)
class ViolationType(str, Enum):
    number_mismatch = "number_mismatch"        # 재계산값과 다름
    missing_evidence = "missing_evidence"      # 수치 문장인데 claim_id 없음/미존재
    banned_phrase = "banned_phrase"            # 금지 해석 표현
    constraint_violation = "constraint_violation"  # 예산·인력·변경가능항목 위반
    past_as_current = "past_as_current"        # 과거 참고 근거를 현재처럼 서술


class Violation(_Strict):
    type: ViolationType
    section: str
    sentence: str
    reason: str


class VerifyResult(_Strict):
    passed: bool
    violations: list[Violation] = Field(default_factory=list)
    checked_claims: int = 0
