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
