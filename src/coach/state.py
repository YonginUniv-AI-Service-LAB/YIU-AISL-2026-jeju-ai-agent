"""LangGraph 공유 상태. 노드는 자기 담당 키만 쓴다(★ 표시가 소유자)."""
from __future__ import annotations

from typing import Any, Literal, Optional, TypedDict


class CoachState(TypedDict, total=False):
    # 입력
    question: str
    store_input: dict            # ★B  StoreInput.model_dump(mode="json")
    session_id: str
    previous_experiment: Optional[dict]  # ★B  2주 후 재방문 시 직전 Experiment
    # B
    missing_slots: list[str]     # ★B
    ask_back: Optional[str]      # ★B  되묻기 문장(있으면 그래프 종료 후 사용자에게)
    llm_available: bool          # ★B
    # A
    analysis_result: dict        # ★A  AnalysisResult
    # C
    report: dict                 # ★C  Report
    verify_result: dict          # ★C  VerifyResult
    retries: int                 # ★B  재생성 횟수(최대 MAX_RETRIES)
    experiment_review: Optional[dict]  # ★B  직전 실험 결과 점검
    debug_inject: Optional[str]  # 테스트용 결함 주입(예: "banned_phrase")
    # 공통
    trace: list[dict[str, Any]]  # 모든 노드: 실행 로그(재현성)
    status: Literal["need_input", "done", "done_with_drops", "fallback"]
