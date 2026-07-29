# Smoke 001 결과

## 결론

pipeline 배관과 Y/N/U gate는 작동했지만 검색·답변 품질은 아직 통과하지 못했습니다. 이 결과로 제품 수준이나 BG3 전체 정확도를 주장하지 않습니다.

## 범위

- 실행 모드: `smoke`
- 후보: 10개 사실군, 40개 질의
- 사람 입력: reference 10회와 질의 표현 40회를 Y/N/U 또는 고정 사유로 판정
- 승인: 네 표현이 모두 승인된 3개 사실군, 12개 질의
- 로컬 corpus: 공개 Namu Wiki snapshot 40문서·1,024청크
- 검색: dependency-free BM25 lexical baseline, top-5
- 생성: local Qwen2.5 7.6B Q4_K_M, localhost-only 구성. 외부 LLM token은 사용하지 않았으나 OS 수준 outbound 관측은 수행하지 않음
- reference 성격: 동일 공개 snapshot에서 만든 `synthetic_self_retrieval_diagnostic`; 독립 웹 정답 실험이 아님

## 관측값

| 항목 | 결과 |
|---|---:|
| 실행 오류 | 0 / 12 |
| answered | 7 / 12 |
| abstain | 5 / 12 |
| source chunk top-5 hit | 8 / 12 |
| latency p50 | 2,916.8 ms |
| latency max | 3,456.4 ms |

질의 유형별 source chunk top-5 hit:

| 유형 | hit / n |
|---|---:|
| natural | 1 / 3 |
| mixed | 2 / 3 |
| spacing_abbreviation | 3 / 3 |
| paraphrase | 2 / 3 |

표본은 유형별 3개뿐이므로 비율을 일반화하면 안 됩니다.

## 확인된 실패

1. 자연어 질의 두 건은 source chunk가 top-5에 없었습니다. 현재 한국어 어절 기반 BM25가 표현 변형에 취약하다는 진단 신호입니다.
2. source chunk가 top-5에 있어도 한 건은 abstain했습니다. 이는 검색 성공 뒤의 생성 실패입니다.
3. source chunk가 4위에 있던 실행 모드 질의 한 건은 더 높은 순위의 다른 문서를 따라 공식·비공식 모드라고 답했습니다. candidate ranking 또는 evidence selection 실패입니다.
4. 최초 local QA는 긴 citation ID 복사 실패가 3회 연속 발생해 circuit breaker가 중단했습니다. 모델에는 `C1`~`C5`만 전달하고 runner가 안정 ID로 변환하도록 수정한 뒤 12건이 오류 없이 완료됐습니다.
5. review 중 일부 문자가 Windows CP949 console에 표시되지 않아 중단됐습니다. UTF-8/replace 출력으로 수정했고 append-only event에서 이어서 재개했습니다.

## 다음 gate

- 32건 diagnostic에서 lexical baseline 실패를 재확인합니다.
- 다음 검색 arm은 모델 생성 query expansion이 아니라 token-free 문자 n-gram 또는 local dense retrieval을 우선 비교합니다.
- answer 평가는 source-hit과 분리하고 claim/citation Y/N/U를 추가합니다.
- 독립 인터넷 reference branch와 4시간 soak가 구현되기 전에는 1,000건 full 결과를 최종 평가로 부르지 않습니다.

## 라이선스 경계

상세 보고서의 데이터 발췌·요약은 각 REFERENCE 원문에서 변경되었으며 CC BY-NC-SA 2.0 KR 조건을 따릅니다. 저장소의 MIT 라이선스는 코드에만 적용됩니다.
