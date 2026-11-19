# jeju-coach — 제주 가게 운영실험 코치

카페 점주가 고민을 말하면, **제주데이터허브 읍면동 유동인구(No.753·748)와 기상청 강수 기록**을 점주의 최근 12주 주문과 비교해 "동네가 줄었는지, 나만 줄었는지"를 가리고, 예산·인력 안에서 **2주짜리 운영 실험 하나**를 추천하는 AI 에이전트입니다. 2주 뒤 코드로 다시 오면 실험 효과를 점검합니다.

## 빠른 시작
```powershell
# Docker (권장) — ZIP을 푼 폴더에서
docker build -t jeju-coach .
docker run --rm --network none jeju-coach                     # 재현: 인터넷 없이 검증 → 테스트 → 평가 → 데모
docker run --rm -p 8501:8501 jeju-coach python tasks.py app   # 점주용 화면 → http://localhost:8501

# Python 3.14 (Docker가 없을 때)
python -m venv .venv
.venv\Scripts\python tasks.py setup
.venv\Scripts\python tasks.py reproduce
.venv\Scripts\python tasks.py app
```
키가 하나도 없어도 전체 기능이 동작합니다(기준일 스냅샷 + 규칙 기반). 설정은 `.env.example`을 `.env`로 복사해서 바꿉니다.

## 제출 문서 (`submission/`)
| 문서 | 내용 |
|---|---|
| `01_실행환경_매뉴얼.md` | 설치·실행 절차, 의존성, 환경변수, API 키 발급 방법, 평가용 LLM 키, 사용자 매뉴얼, 오류·제약사항 |
| `02_데이터_활용_명세.md` | 필수·외부 데이터, 출처·이용조건, 전처리·분석 흐름, 외부 서비스·오픈소스 목록 |
| `03_결과_설명자료.md` | 문제 정의, Agent 구조, 주요 기능, 실행 결과, 검증 방법·한계, 센터 활용 방안 |
| `04_AI_활용_내역.md` | 서비스 내 AI(Gemini), 개발에 쓴 AI 도구, 외부 API·데이터·코드 |

## 그래프
```
START → parse_input ─┬─(빠진 조건)→ ask_back → END
                     └→ check_fitness → analyze ─┬─(재방문)→ review_experiment ─┐
                                                 └──────────────────────────────┴→ design_experiment
   → compose_report → verify ─┬─ 통과 → finish → END
            ↑   bump_retry ←──┤ 위반 & retries<2
                              └─ 위반 & retries=2 → drop_violations → END
```

## 담당
| 담당 | 파일 |
|---|---|
| **A** 데이터·분석 | `nodes/a_data.py`, `data/`, `config.THRESHOLDS`, `tasks.py`, `scripts/` |
| **B** 오케스트레이터·화면 | `graph.py`, `state.py`, `session.py`, `nodes/b_orchestrator.py`, `llm/client.py`, `app/streamlit_app.py` |
| **C** 실험·검증 | `nodes/c_experiment.py`, `mocks/catalog.yaml`, `mocks/support_programs.json`, `eval/` |

## 개발 규칙
1. 계약(`contracts.py`) 변경은 3명 합의 + `CONTRACT_VERSION` 올리기 + `export_schemas.py` 실행. 모든 모델은 `extra="forbid"`.
2. 상태 키는 소유자만 씀(`state.py`의 ★ 표시).
3. 수치 계산 함수는 A 소유(`a_data.py`). C의 검증은 `recompute()`를 호출하고 중복 구현하지 않음.
4. 수치가 들어간 리포트 문장은 반드시 `claim_ids`로 근거와 연결. 없으면 verify가 차단.
5. 모든 노드는 `trace.log()`로 실행 로그를 남김(재현성).

## 재현 파이프라인 (A)
지역 지표를 실제 스냅샷(제주데이터허브 No.753·748 + 기상청 ASOS)에서 계산합니다. **API는 `snapshot` 단계에서만** 부르고, 나머지는 스냅샷만 읽습니다.
Windows 는 `python tasks.py <대상>`, make 가 있는 환경(Docker·Linux)은 `make <대상>` — 같은 동작입니다.

| 대상 | 하는 일 | 네트워크 |
|---|---|---|
| `setup` | 의존성 설치 (`requirements.lock`: 정확한 버전 + 해시 검사) | 패키지 설치만 |
| `lock` | `requirements.txt` 를 고친 뒤 `requirements.lock` 다시 만들기 (uv, Windows·Linux 공용) | 패키지 정보 조회 |
| `snapshot --as-of 2026-08-31` | API → `data/raw/fetch/` → `data/snapshot/<AS_OF>/*.parquet` + `manifest.json` → 검증 | **사용(키 필요)** |
| `validate` | 스냅샷 검증 게이트 6개(해시·스키마·날짜 연속성·시범 지역·외국인 0값·지연). FAIL 이면 종료 코드 1 | 없음 |
| `test` | mock 테스트 + (스냅샷 있으면) 실데이터 통합 테스트 | 없음 |
| `eval` | 평가 실행 + **실행 기록**(`eval/runs/`: 코드·데이터·설정·환경 + 지표 + 예측, MLflow 있으면 같이) | 없음 |
| `demo` | 사람이 읽는 실데이터 시나리오 출력 | 없음 |
| `live [--shift-weeks N]` | **라이브 모드**: 점주 주문 기록 시기에 맞춰 그 동네·그 기간 데이터를 API로 조회해 분석 | **사용(키 필요)** |
| `reproduce` | validate → test → eval → demo. 앞 단계가 실패하면 멈춤 | **차단됨** |

```bash
# 스냅샷은 저장소에 없음(재배포 조건 확인 전). 팀에서 받은 data/snapshot/2026-08-31/ 폴더를 넣거나 직접 만들기:
python tasks.py snapshot --as-of 2026-08-31   # .env 에 JEJU_DATAHUB_KEY, DATA_GO_KR_API_KEY 필요, 서버 불안정으로 약 10분
python tasks.py reproduce                     # 약 15초, 네트워크 없이 전체 재현
```
- 재현 단계는 `COACH_LLM=0`, `COACH_OFFLINE=1`(우리 API 함수가 호출 거부), 프록시를 닫힌 포트로 설정(다른 라이브러리의 접속도 실패)해서 실행합니다.
- 기준일은 `--as-of`(또는 환경변수 `COACH_AS_OF`) 한 곳에서만 정하고, 비교 창·다운로드 기간은 자동 계산됩니다.

### 라이브 모드 (`COACH_MOCK=0 COACH_LIVE=1`)
점주가 입력한 주문 기록의 마지막 주를 기준일로 삼아, **그 읍면동·그 기간의 동네 흐름을 API로 불러와** 분석합니다. 계산은 스냅샷 모드와 같은 함수라, 같은 기간이면 결과도 같습니다.
- 원천의 마지막 관측일보다 늦은 기간을 물으면 동네 창은 마지막 관측일까지만 쓰고, 적합성 판정(F5 지연·F7 기간 불일치)이 그 사실을 안내합니다
- 같은 동네·기간은 캐시(`data/live/cache/`)로 즉시 응답, 조회한 데이터는 `data/live/snapshots/`에 manifest·snapshot_id 와 함께 저장(라이브 응답도 재현 가능)
- 조회 실패(서버 오류·키 없음·오프라인)면 기준일 스냅샷으로 분석하고 그 사유를 리포트 '한계'에 남깁니다
- 재현 단계(`reproduce`·`test`)는 항상 스냅샷 모드로 고정됩니다
- 기준일 2026-08-31(753·748 실제 마지막 관측일), 시범 시나리오 only_me·노형동. 결정 근거: 노션 A파트 「decisions」, 데이터 명세: 노션 A파트 「data_spec」
- `COACH_MOCK=1`(기본)에서는 실데이터 테스트가 자동으로 건너뜁니다.
- 읍면동 코드는 행정안전부 행정동코드(KIKmix 2026-07-01)로 교체됨: 애월읍 5011025300 · 노형동 5011066000 · 한림읍 5011025000 · 연동 5011065000

### 평가·실행 기록
- A 평가: `eval/cases_a.jsonl` 7문항(적합성 판정 F2~F7·성수기 착시·임계값 경계, 라이브 동결 데이터 재생 1문항) + 개발 사례(합성 4유형). 정답은 실행 전에 규칙·데이터 사실로 정했고 임계값 조정에 쓰지 않습니다. **정확도는 합성 사례 기준이며 실제 점주 대상 정확도가 아닙니다.**
- 실행마다 `eval/runs/local/<UTC시각>_<커밋>_<노드>/`(git 제외)에, `python tasks.py eval --official`이면 공식 기록으로 `eval/runs/`(커밋 대상)에 `run_meta.json`(커밋·snapshot_id·config_hash·Python·lock 지문) · `metrics.json` · `predictions.jsonl`, 그리고 `eval/runs/index.jsonl`에 한 줄. **같은 코드·데이터·설정이면 `predictions_sha256`이 같습니다.**
- (선택) `python -m pip install -r requirements-dev.txt`로 MLflow를 설치하면 같은 기록이 로컬 MLflow에도 남고, `mlflow ui --backend-store-uri sqlite:///mlflow.db`로 실행끼리 비교할 수 있습니다(lock·Docker에는 포함하지 않음).

### Docker (심사·새 환경용 — 권장)
```powershell
docker build -t jeju-coach .
docker run --rm --network none jeju-coach                        # 재현: 네트워크를 완전히 끊고 validate → test → eval → demo
docker run --rm -p 8501:8501 jeju-coach python tasks.py app      # 점주용 화면 → 브라우저 http://localhost:8501
docker run --rm --env-file .env jeju-coach python tasks.py live  # (선택) 라이브 모드 — 키·네트워크 필요
```
- 베이스 `python:3.14.10-slim`(버전 고정), `requirements.lock`으로 해시 검사 설치. 빌드 약 2분, 이미지 약 970MB
- `.env`(키)는 이미지에 넣지 않습니다(`--env-file`로만 주입). 스냅샷(`data/snapshot/`)·라이브 동결 데이터(`data/live/snapshots/`)는 빌드할 때 있으면 포함 → **데이터가 든 이미지이므로 공개 레지스트리에 올리지 않습니다**
- 이 PC 확인: 컨테이너 안 `reproduce` 21초 통과(평가 7/7), 화면 health 200

### CI (GitHub Actions)
PR·main push마다 `.github/workflows/ci.yml`이 ① Python 3.14 + lock 설치 → `python tasks.py test` ② Docker 빌드 → 컨테이너 안 테스트(네트워크 차단)를 자동으로 돌립니다. GitHub에는 키·스냅샷이 없어 mock 테스트만 돌고 실데이터 테스트는 건너뜁니다.

### 넣지 않은 도구와 이유
Airflow·Kubernetes·feature store·DVC 원격 저장소는 넣지 않았습니다. 데이터 수집은 하루 한 번 이하·4개 읍면동 규모라 `tasks.py snapshot` 한 명령으로 충분하고, 센터 PC(i7-8700/RAM 32GB)와 일정에서 운영 부담이 이득보다 큽니다. 대신 manifest 해시·검증 게이트·lock·Docker·실행 기록으로 같은 목적(재현·추적)을 가볍게 달성했습니다.
