# jeju-coach 파이프라인 — 실제 동작은 tasks.py. Windows 에서는 `python tasks.py <대상>` 을 쓰세요.
#   make snapshot AS_OF=2026-08-31   # API 호출(키 필요, 유일한 네트워크 단계)
#   make reproduce                   # validate → test → eval → demo, 네트워크 없이
PY ?= python
AS_OF ?= 2026-08-31

.PHONY: setup lock snapshot validate test eval demo live app reproduce

setup:
	$(PY) tasks.py setup --as-of $(AS_OF)

snapshot:
	$(PY) tasks.py snapshot --as-of $(AS_OF)

validate:
	$(PY) tasks.py validate --as-of $(AS_OF)

test:
	$(PY) tasks.py test --as-of $(AS_OF)

eval:
	$(PY) tasks.py eval --as-of $(AS_OF)

demo:
	$(PY) tasks.py demo --as-of $(AS_OF)

reproduce:
	$(PY) tasks.py reproduce --as-of $(AS_OF)

lock:
	$(PY) tasks.py lock

live:
	$(PY) tasks.py live

app:
	$(PY) tasks.py app

