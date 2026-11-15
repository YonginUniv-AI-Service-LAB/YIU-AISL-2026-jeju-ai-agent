"""테스트는 외부 LLM 없이 항상 같은 결과가 나오게 한다.
.env 의 COACH_LLM 값과 상관없이 끈다 (load_dotenv 는 이미 있는 환경변수를 덮어쓰지 않음).
LLM 경로는 test_b_extract.py 에서 가짜 응답(monkeypatch)으로 따로 검사한다."""
import os

os.environ["COACH_LLM"] = "0"