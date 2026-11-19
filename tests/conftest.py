"""테스트는 기본적으로 외부 LLM 없이 항상 같은 결과가 나오게 한다.
.env 의 COACH_LLM 값은 무시한다 (load_dotenv 는 이미 있는 환경변수를 덮어쓰지 않음).
실제 LLM으로 테스트하고 싶으면 터미널에서 직접 켠다:  set "COACH_LLM=1" && python -m pytest -q
LLM 경로는 test_b_extract.py 에서 가짜 응답(monkeypatch)으로 따로 검사한다."""
import os

os.environ.setdefault("COACH_LLM", "0")
# 테스트는 지정하지 않으면 mock(.env 의 COACH_MOCK=0 보다 우선). 실데이터 테스트는 COACH_MOCK=0 을 셸에서 지정(tasks.py test 가 자동 처리)
os.environ.setdefault("COACH_MOCK", "1")
