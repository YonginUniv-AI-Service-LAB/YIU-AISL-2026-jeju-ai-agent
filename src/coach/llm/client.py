"""B LLM 클라이언트 — 회사별 호출을 한 곳에 모은다.

사용:  extract_structured(prompt, SomePydanticModel) -> SomePydanticModel 인스턴스
실패(키 없음·네트워크·한도 초과·형식 오류)는 모두 LLMError 로 올린다.
→ 호출하는 쪽(b_orchestrator)이 규칙 기반 경로로 대체한다.

환경변수(.env):
  LLM_PROVIDER = gemini            (기본값)
  LLM_API_KEY  = ...               (Google AI Studio 키)
  LLM_MODEL    = gemini-3.5-flash-lite (기본값)
  LLM_TIMEOUT_S = 15
"""
from __future__ import annotations

import os
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = {"gemini": "gemini-3.5-flash-lite"}
LAST_LATENCY_S: float | None = None  # 마지막 호출 응답 시간(로그용)


class LLMError(RuntimeError):
    pass


def provider() -> str:
    return os.getenv("LLM_PROVIDER", "gemini").strip().lower()


def model_name() -> str:
    return os.getenv("LLM_MODEL") or DEFAULT_MODEL.get(provider(), "")


def extract_structured(prompt: str, schema: type[T]) -> T:
    """prompt 를 보내고 schema 형식(JSON)으로 받아 검증해서 돌려준다."""
    key = os.getenv("LLM_API_KEY")
    if not key:     # 키 없는 팀원도 규칙 기반으로 돌아가게
        raise LLMError("LLM_API_KEY 가 설정되지 않음")
    p = provider()
    t0 = time.perf_counter()
    if p == "gemini":
        raw = _gemini(prompt, schema, key)
    elif p == "school":
        raw = _school(prompt, schema, key)
    else:
        raise LLMError(f"지원하지 않는 LLM_PROVIDER: {p}")
    try:    # 형식이 틀리면 여기서 걸림
        out = schema.model_validate_json(raw)
    except ValidationError as e:
        raise LLMError(f"응답 형식 오류: {e.errors()[:2]}") from e
    global LAST_LATENCY_S
    LAST_LATENCY_S = round(time.perf_counter() - t0, 2)
    return out

# 일시 오류: 서버 과부하(503 UNAVAILABLE)·순간 한도 초과(429 RESOURCE_EXHAUSTED)
_RETRY_MARKERS = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED")


def _retry_once(call, wait_s: float = 1.0):
    """일시 오류면 잠깐 쉬고 딱 1번만 다시 시도. 키 오류 같은 나머지는 바로 넘김(규칙 기반으로 대체)."""
    try:
        return call()
    except Exception as e:
        if not any(m in str(e) for m in _RETRY_MARKERS):
            raise
        time.sleep(wait_s)
        return call()

def _gemini(prompt: str, schema: type[BaseModel], key: str) -> str:
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise LLMError("google-genai 미설치: pip install google-genai") from e
    timeout_ms = int(float(os.getenv("LLM_TIMEOUT_S", "15")) * 1000)
    try:
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))
        resp = _retry_once(lambda: client.models.generate_content(
            model=model_name(),
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,  # 구조화 출력: 이 형식으로만 답하게
                temperature=0,           # 같은 입력 → 최대한 같은 출력(재현성)
            ),
        ))
    except Exception as e:  # 네트워크·인증·한도 초과 등
        raise LLMError(f"Gemini 호출 실패: {type(e).__name__}: {e}") from e
    if not resp.text:
        raise LLMError("Gemini 빈 응답")
    return resp.text


def _school(prompt: str, schema: type[BaseModel], key: str) -> str:
    # TODO(B): 학교 AI챗 API(Claude) — 호출 방식(엔드포인트·요청 형식) 확인 후 구현.
    #  응답은 schema 에 맞는 JSON 문자열을 돌려주면 된다.
    raise LLMError("학교 API 연결 미구현")