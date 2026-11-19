# jeju-coach — 재현·데모용 이미지 (Python 3.14.10 고정, requirements.lock 으로 해시 검사 설치)
#   빌드:  docker build -t jeju-coach --build-arg GIT_COMMIT=<커밋> .
#   재현:  docker run --rm --network none jeju-coach                  (네트워크 없이 validate → test → eval → demo)
#   화면:  docker run --rm -p 8501:8501 jeju-coach python tasks.py app  → 브라우저 http://localhost:8501
#   라이브: docker run --rm --env-file .env jeju-coach python tasks.py live   (키·네트워크 필요, .env 는 이미지에 넣지 않음)
FROM python:3.14.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    COACH_MOCK=0 COACH_LLM=0 COACH_LIVE=0 MLFLOW_DISABLE_AGENT_HINT=1

WORKDIR /app

# 의존성 먼저(코드가 바뀌어도 이 층은 캐시로 재사용 → 다시 빌드가 빠름)
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock

# 코드 + (있으면) 스냅샷. .env·원본 API 응답·라이브 캐시·로컬 기록은 .dockerignore 로 제외
COPY . .

# 컨테이너엔 git 이 없으므로 실행 기록(run_meta)의 커밋 번호를 빌드 때 넣어 준다
ARG GIT_COMMIT=unknown
ENV COACH_GIT_COMMIT=${GIT_COMMIT}

EXPOSE 8501
CMD ["python", "tasks.py", "reproduce"]
