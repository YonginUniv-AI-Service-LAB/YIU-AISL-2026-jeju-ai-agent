"""jeju-coach 파이프라인 실행기 — Windows 에서도 `make` 없이 같은 명령을 쓰기 위한 파일.

    python tasks.py setup                       의존성 설치 (requirements.lock 해시 검사, 없으면 requirements.txt)
    python tasks.py lock                        requirements.txt → requirements.lock 다시 만들기 (라이브러리 추가 후)
    python tasks.py snapshot --as-of 2026-08-31 API 호출 → data/snapshot/<AS_OF>/ + manifest  ← 키가 필요한 유일한 단계
    python tasks.py validate [--as-of …]        스냅샷 검증 게이트 (FAIL 이면 종료 코드 1)
    python tasks.py test                        mock 테스트 + (스냅샷 있으면) 실데이터 통합 테스트
    python tasks.py eval                        평가 실행 (현재: B·C 평가 스크립트, S10 에서 A 평가·실행 기록 추가)
    python tasks.py demo                        사람이 읽는 실데이터 시나리오 출력
    python tasks.py reproduce                   validate → test → eval → demo, 네트워크 없이

Makefile 의 같은 이름 대상은 모두 이 파일을 부른다(Docker·Linux 용 껍데기).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable
DEFAULT_AS_OF = "2026-08-31"

# 재현 단계(validate·test·eval·demo)에서 쓰는 환경: LLM 끔 + 네트워크 차단 두 겹
#  1) COACH_OFFLINE=1 → 우리 API 함수(probe_apis.call/call_asos)가 호출을 거부
#  2) 프록시를 닫힌 포트로 → requests/httpx 를 쓰는 어떤 라이브러리가 몰래 접속해도 연결 실패
OFFLINE_ENV = {"COACH_LLM": "0", "COACH_OFFLINE": "1",
               "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "NO_PROXY": ""}


def sh(title: str, args: list[str], **env) -> None:
    """한 단계 실행. 실패하면 그 자리에서 종료 코드를 그대로 돌려주며 멈춘다(뒤 단계 실행 안 함)."""
    print(f"\n━━ {title}\n   $ {' '.join(Path(a).name if a == PY else a for a in args)}", flush=True)
    t0 = time.perf_counter()
    r = subprocess.run(args, cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8", **env})
    print(f"   ({time.perf_counter() - t0:.1f}s, 종료 코드 {r.returncode})", flush=True)
    if r.returncode != 0:
        sys.exit(r.returncode)


def snapshot_dir(as_of: str) -> Path:
    return ROOT / "data" / "snapshot" / as_of


# ── 대상들
def setup(a):
    lock = ROOT / "requirements.lock"
    if lock.exists():   # 해시가 있으므로 pip 가 받은 파일의 지문까지 검사(다르면 설치 거부)
        sh("setup (requirements.lock, 해시 검사)", [PY, "-m", "pip", "install", "--require-hashes", "-r", str(lock)])
    else:
        sh("setup (requirements.txt — lock 없음)", [PY, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])


def lock(a):
    """requirements.txt(사람이 고치는 목록) → requirements.lock(정확한 버전+해시). Windows·Linux 공용."""
    sh("lock 1/2 uv 준비", [PY, "-m", "pip", "install", "-q", "uv"])
    sh("lock 2/2 requirements.lock 생성", [PY, "-m", "uv", "pip", "compile", "requirements.txt", "--universal",
                                          "--python-version", "3.14", "--generate-hashes", "-o", "requirements.lock"])


def snapshot(a):
    env = {"COACH_AS_OF": a.as_of}
    sh("snapshot 1/3 API → data/raw/fetch (키 필요)", [PY, "scripts/fetch_snapshot.py"], **env)
    sh("snapshot 2/3 원본 → parquet + manifest", [PY, "scripts/build_snapshot.py"], **env)
    sh("snapshot 3/3 검증", [PY, "scripts/validate_snapshot.py"], **env)


def validate(a):
    sh("validate 스냅샷 검증 게이트", [PY, "scripts/validate_snapshot.py"], COACH_AS_OF=a.as_of, **OFFLINE_ENV)


def test(a):
    sh("test 1/2 mock 테스트", [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"], COACH_MOCK="1", **OFFLINE_ENV)
    if (snapshot_dir(a.as_of) / "manifest.json").exists():
        sh("test 2/2 실데이터 통합 테스트", [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                     "tests/test_integration_real.py"],
           COACH_MOCK="0", COACH_AS_OF=a.as_of, **OFFLINE_ENV)
    else:
        print(f"\n━━ test 2/2 건너뜀: {snapshot_dir(a.as_of).relative_to(ROOT)} 없음 "
              f"(python tasks.py snapshot --as-of {a.as_of} 또는 팀에서 받은 폴더를 넣으세요)")


def eval_(a):
    # TODO(A, S10): A 평가 문항 + eval/runs/<시각>_<커밋>/ 실행 기록(metrics·run_meta·predictions)
    sh("eval 1/2 전체 평가 (C 하네스 초안, mock)", [PY, "eval/run_eval.py"], COACH_MOCK="1", **OFFLINE_ENV)
    sh("eval 2/2 B 조건 추출 평가 (규칙 기반)", [PY, "eval/run_eval_b.py"], COACH_MOCK="1", **OFFLINE_ENV)


def demo(a):
    sh("demo 실데이터 시나리오", [PY, "scripts/run_real_scenario.py"], COACH_MOCK="0", COACH_AS_OF=a.as_of,
       **OFFLINE_ENV)


def reproduce(a):
    t0 = time.perf_counter()
    for step in (validate, test, eval_, demo):
        step(a)
    print(f"\n✔ reproduce 완료 ({time.perf_counter() - t0:.1f}s) — 네트워크 없이 validate → test → eval → demo")


TARGETS = {"setup": setup, "lock": lock, "snapshot": snapshot, "validate": validate, "test": test, "eval": eval_,
           "demo": demo, "reproduce": reproduce}

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="jeju-coach 파이프라인 (Makefile 과 같은 대상)")
    ap.add_argument("target", choices=TARGETS)
    ap.add_argument("--as-of", default=os.getenv("COACH_AS_OF", DEFAULT_AS_OF), help="기준일 YYYY-MM-DD")
    args = ap.parse_args()
    TARGETS[args.target](args)
