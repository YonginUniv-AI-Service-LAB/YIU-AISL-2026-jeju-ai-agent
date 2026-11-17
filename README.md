# jeju-coach — 제주 가게 운영실험 코치 (에이전트 뼈대 v0.1)

모든 노드가 **mock 모드로 처음부터 끝까지 동작**합니다. 각자 자기 파일의 `TODO(A|B|C)`만 실제 구현으로 바꾸면 됩니다.

```bash
pip install -r requirements.txt
python -m pytest -q                 # 16개 테스트
python scripts/run_demo.py          # 합성 점주 4유형 + 되묻기 + 검증 루프
python eval/run_eval.py             # 평가 하네스 초안
python scripts/export_schemas.py    # contracts.py → schemas/*.json
```

## 그래프
```
START → parse_input ─┬─(빠진 조건)→ ask_back → END
                     ├─(재방문)→ review_experiment ─┐
                     └────────────────────────────→ check_fitness → analyze
   → design_experiment → compose_report → verify ─┬─ 통과 → finish → END
                              ↑   bump_retry ←───┤ 위반 & retries<2
                                                  └─ 위반 & retries=2 → drop_violations → END
```

## 담당자별 파일
| 담당 | 파일 | mock → 실제로 바꿀 것 |
|---|---|---|
| **A** 데이터·분석 | `nodes/a_data.py`, `mocks/region_mock.json`, `mocks/dong_codes.json`, `config.THRESHOLDS` | `_region()`을 No.753·748·756 스냅샷(DuckDB) 계산으로 교체, 적합성 판정에 LLM 데이터 선택 추가, 스냅샷 해시 |
| **B** 오케스트레이터 | `graph.py`, `state.py`, `nodes/b_orchestrator.py` | `_llm_extract()` 구조화 출력, 되묻기 문장 LLM화, 세션 저장(재방문), LLM 장애 시 대체 경로 |
| **C** 실험·검증 | `nodes/c_experiment.py`, `mocks/catalog.yaml`, `mocks/support_programs.json`, `mocks/synthetic_stores.json`, `eval/` | 카탈로그 15개, 랭킹, LLM 문장 다듬기(claim_id만 인용) + 위반 사유 피드백, 지원제도 RAG, 평가 하네스 확장 |

## 규칙
1. **계약(`contracts.py`) 변경 = 3명 합의 + `CONTRACT_VERSION` 올리기 + `export_schemas.py` 실행.** 모든 모델은 `extra="forbid"`.
2. 상태 키는 **소유자만 씀**(`state.py`의 ★ 표시).
3. 수치 계산 함수는 A 소유(`a_data.py`). C의 검증은 `recompute()`를 호출, 중복 구현 금지.
4. 수치가 들어간 리포트 문장은 반드시 `claim_ids` 연결. 없으면 verify가 차단.
5. 모든 노드는 `trace.log()`로 실행 로그를 남김(재현성).
6. mock 값(`region_mock.json`)은 **실측이 아님**. 제출물에 mock 수치가 남지 않게 할 것.

## 알려진 한계(초안)
- `classify()` 임계값은 합성 데이터 4유형이 갈리도록 잡은 초기값입니다.
- 재방문 점검은 단일 점포 전후 비교로, 효과를 확정하지 않습니다.
- `drop_violations`는 섹션 문장만 제거합니다. 실험(experiment) 제약 위반은 설계 단계 필터로 사전 차단합니다.

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
| `eval` | 평가 실행 (B·C 평가, A 평가는 S10에서 추가) | 없음 |
| `demo` | 사람이 읽는 실데이터 시나리오 출력 | 없음 |
| `reproduce` | validate → test → eval → demo. 앞 단계가 실패하면 멈춤 | **차단됨** |

```bash
# 스냅샷은 저장소에 없음(재배포 조건 확인 전). 팀에서 받은 data/snapshot/2026-08-31/ 폴더를 넣거나 직접 만들기:
python tasks.py snapshot --as-of 2026-08-31   # .env 에 JEJU_DATAHUB_KEY, DATA_GO_KR_API_KEY 필요, 서버 불안정으로 약 10분
python tasks.py reproduce                     # 약 15초, 네트워크 없이 전체 재현
```
- 재현 단계는 `COACH_LLM=0`, `COACH_OFFLINE=1`(우리 API 함수가 호출 거부), 프록시를 닫힌 포트로 설정(다른 라이브러리의 접속도 실패)해서 실행합니다.
- 기준일은 `--as-of`(또는 환경변수 `COACH_AS_OF`) 한 곳에서만 정하고, 비교 창·다운로드 기간은 자동 계산됩니다.
- 기준일 2026-08-31(753·748 실제 마지막 관측일), 시범 시나리오 only_me·노형동. 결정 근거: 노션 A파트 「decisions」, 데이터 명세: 노션 A파트 「data_spec」
- `COACH_MOCK=1`(기본)에서는 실데이터 테스트가 자동으로 건너뜁니다.
- 읍면동 코드는 행정안전부 행정동코드(KIKmix 2026-07-01)로 교체됨: 애월읍 5011025300 · 노형동 5011066000 · 한림읍 5011025000 · 연동 5011065000
