"""B 실제 LLM 추출 확인용(키 필요). 사용: python scripts/try_llm.py ["직접 넣을 문장"]

문장마다 LLM 추출 결과·응답 시간·규칙 추출 결과를 나란히 보여준다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from coach.llm import client as llm
from coach.nodes import b_orchestrator as B

SAMPLES = [
    "제주시 애월읍에서 카페를 해요. 최근 손님이 줄었어요. 예산은 30만 원, 오전 2명 오후 1명이에요.",
    "노형동 카페 사장인데 혼자 해요. 메뉴랑 가격은 바꿀 수 있어요.",
    "한림에서 작은 카페 운영중. 오전엔 둘, 오후엔 셋. 배달도 해볼 생각 있어요. 예산 없음",
    "카페 매출이 줄었는데 뭐 해볼까요?",
    "서귀포 중문에서 카페해요 예산 20만",  # 허용 지역 밖 → 지역 없음이 정답
]

print(f"provider={llm.provider()} model={llm.model_name()}\n")
for q in sys.argv[1:] or SAMPLES:  # 문장을 직접 넣으면 그것만, 아니면 SAMPLES 전부
    print("Q:", q)
    try:
        print("  LLM :", B._llm_extract(q), f"({llm.LAST_LATENCY_S}s)")
    except Exception as e:
        print("  LLM 실패:", e)
    print("  규칙:", B._rule_extract(q), "\n")