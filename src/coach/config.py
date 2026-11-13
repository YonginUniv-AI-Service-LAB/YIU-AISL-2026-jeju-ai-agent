import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

try:  # .env 가 있으면 환경변수로 읽기(없어도 동작)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

MOCK_DIR = Path(__file__).parent / "mocks"
PROMPT_DIR = Path(__file__).parent / "prompts"
USE_MOCK = os.getenv("COACH_MOCK", "1") == "1"   # 실제 데이터 붙이면 0
# B: LLM 사용 여부. 데이터 mock 여부와 따로 켤 수 있음(가짜 데이터 + 진짜 LLM 테스트용)
USE_LLM = os.getenv("COACH_LLM", "0" if USE_MOCK else "1") == "1"
MAX_RETRIES = 2

# A 소유: 진단 임계값(합성 점주 4유형으로 튜닝)
THRESHOLDS = {
    "my_decline_pct": -10.0,     # 내 주문이 이보다 더 떨어지면 '하락'
    "region_flat_pct": -5.0,     # 지역 변화가 이보다 크면(덜 떨어지면) '유지'
    "co_decline_gap_pp": 10.0,   # 내 하락과 지역 하락 차이가 이 이내면 '동반'
}

# C 소유: 금지 해석 표현(기획서 8장)
BANNED_PHRASES = ["반경 500m", "반경500m", "구매전환율", "고유 방문자", "원인은", "때문입니다", "원인이 확인", "매장 방문객 수는"]
