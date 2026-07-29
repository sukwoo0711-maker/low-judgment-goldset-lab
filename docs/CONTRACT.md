# 평가 계약

## 목표

현재 로컬 corpus 기반 QA의 실패를 `corpus 부재`, `검색 실패`, `생성 실패`, `인용 실패`, `reference 불확실성`으로 분리한다.

## 비목표

- 자동으로 완전한 정답을 생성하는 시스템
- LLM 단독 자동채점
- 실시간 웹+로컬 혼합 답변
- 신규 vector DB 또는 대형 orchestration framework
- 작은 표본으로 제품 수준 품질을 주장하는 것

## 고정 불변조건

1. 로컬 QA 입력에는 자연 질의만 들어간다.
2. 웹 passage, reference answer, predicate, oracle term은 QA prompt·index·glossary·retriever tuning에 들어가지 않는다. QA 입력 manifest는 자연질의 파일과 corpus snapshot만 allowlist한다.
3. 웹 입력과 검색 문서는 명령이 아닌 untrusted evidence로 취급한다.
4. QA 모델은 tool과 외부 network 권한을 갖지 않는다.
5. 모든 답변 주장은 유효한 로컬 chunk ID를 인용하거나 전체 답변을 기권한다.
6. 현재 retriever의 실패만으로 corpus 부재를 확정하지 않는다.
7. 사내 데이터와 machine-specific path는 공개 저장소에 기록하지 않는다.
8. 공개 fixture는 승인된 공개 URL 또는 합성 template에서만 만든다. private 질문을 익명화·치환해 공개 fixture로 만드는 경로는 금지한다.
9. reference canary token이 QA index, prompt, glossary, retriever config, result에 나타나면 실행을 무효화한다.

## 판정 단위

도구가 출처 passage에서 predicate 후보를 제시하고, 질문 하나가 여러 사실을 요구하면 후보를 원자 단위로 나눕니다. 사람은 후보별 `필수/선택/제외`와 다음 값만 선택합니다. 자유서술은 요구하지 않습니다.

- `Y`: 표시된 증거로 판단 가능하고 조건을 만족한다.
- `N`: 표시된 증거로 판단 가능하고 조건을 만족하지 않는다.
- `U`: reference 충돌, 버전 불명, 불충분한 증거 등으로 판단할 수 없다. 이어서 `버전 불명/근거 충돌/표현 불명/전문지식 부족` 중 고정 사유를 고릅니다.

평가 필드는 다음과 같습니다.

- `C`: 독립 evidence pool에서 로컬 corpus 근거가 확인됨. pool 미발견은 `not_found_in_pool`이며 corpus 부재가 아니다. 전수검사 근거가 있을 때만 `confirmed_absent`로 승격한다.
- `R`: 해당 근거가 자연 질의 top-k에 포함됨
- `predicate_answered[predicate]`: 답변이 필수 사실을 언급함
- `predicate_supported[predicate]`: 해당 사실을 로컬 근거가 지지함
- `claim_supported[claim]`: 모델이 추가한 각 주장에 근거가 있음
- `contradiction_present`: reference 또는 로컬 근거와 모순되는 주장이 있음
- `citation_valid[claim]`: 인용 ID가 실제 해당 주장을 지지함
- `abstain_appropriate`: 답변 또는 기권 선택이 근거 상태에 맞음

최종 `A`는 위 원자 판정으로 자동 계산합니다. 사람에게 복합 판정을 요구하지 않습니다. `C=N/U`이면 `R/A`를 자동 생략하고, 기권 답변이면 claim 검수를 생략합니다. Undo를 지원하고 질문당 클릭 수·중앙 판정 시간·반복 항목 일치율을 기록합니다.

`U`는 실패를 숨기는 값이 아닙니다. `U` 비율과 사유를 별도로 보고합니다.

## 독립 evidence pool

`C`는 현재 production 후보 검색기 하나가 아니라 다음 union을 사용해 검토합니다. provenance와 rank를 숨긴 뒤 중복 제거된 evidence card를 한 번에 최대 8개 제시합니다.

- lexical/BM25 또는 FTS
- local dense retrieval
- 승인된 glossary expansion
- 제목·entity 직접 검색
- 평가 전용 oracle term 검색
- 무작위 표본과 lexical hard negative
- 필요한 경우 full-document inspection

oracle 결과는 corpus 진단 상한일 뿐 production 점수와 합산하지 않습니다. oracle/reference를 본 질문은 held-out 평가에서 영구 제외합니다. fixture freeze 시각, glossary 버전, corpus·index·설정 hash를 기록합니다.

`not_found_in_pool`은 `U`로 집계합니다. `confirmed_absent`는 deterministic full-text/entity 검사 또는 지정 문서 전수검토 receipt가 있어야 하며, 그렇지 않으면 corpus gap으로 보고하지 않습니다.

## 평가 규모와 잠정 gate

| 단계 | 규모 | 주장 가능한 범위 |
|---|---:|---|
| smoke | 독립 12문항 | 실행 배관이 작동함 |
| diagnostic | 독립 30문항 | 실패 taxonomy와 다음 투자 대상 |
| held-out pilot | 독립 60~100문항 | 고정 범위에서 변경 전후 신호 탐색 |
| full run | 질의 1,000개 이상 | 사실군·표현군별 실패 분포와 장기 실행 관측 |

잠정 중단 기준:

- corpus coverage `< 50%`: 모델 비교를 멈추고 corpus를 점검
- `C=Y` 중 Retrieval Success@10 `< 70%`: 생성 모델 비교를 멈추고 검색을 점검
- 비기권 답변 중 unsupported/contradictory claim 사건이 1건이라도 있으면 smoke/diagnostic에서 수정 대상으로 기록. held-out의 잠정 경보선은 `> 5%`이지만 제품 안전성 입증으로 해석하지 않음
- 인간 판정 `U > 30%`: reference 또는 질문을 재작성
- 로컬 QA 중 관찰된 외부 request 또는 external token `> 0`: 실행 무효

모든 비율은 원시 건수와 Wilson 또는 명시된 bootstrap 신뢰구간을 함께 기록합니다. query-level 수치와 `fact_cluster_id` 기준 cluster-level 수치를 함께 보고해 paraphrase 복제로 신뢰도를 부풀리지 않습니다. 위 숫자는 초기 진단 gate이며 회사의 acceptance threshold가 아닙니다.

## 1,000건 공개 결과 계약

실행 원본은 schema-validated JSONL로 저장하고, 같은 bytes에서 Markdown을 결정적으로 생성합니다. Markdown에는 누락 없이 각 질의별로 다음을 표시합니다.

- `QUERY`: 로컬 QA에 실제 전달된 원문 그대로
- `RESULT`: 로컬 top-k chunk의 안정 content ID, 순위, bounded excerpt, retrieval arm
- `LOCAL ANSWER`: 로컬 모델의 답 또는 명시적 abstain, 인용 ID
- `INTERNET REFERENCE ANSWER`: 동결된 공개 근거를 요약한 기준답
- `REFERENCE`: canonical URL, 제목, 조회시각, revision/date, content digest, patch/platform 범위
- `LABELS`: 자동 계산 결과와 사람의 Y/N/U 판정이 있으면 그 값
- `fact_cluster_id`, 표현 유형, 실행 설정 hash

인터넷 원문을 대량 복제하지 않습니다. reference answer와 excerpt는 필요한 범위로 요약·제한하고 직접 출처를 연결합니다. `INTERNET REFERENCE ANSWER`는 평가 기준 후보이며 자동 oracle이 아닙니다.

동일 공개 snapshot에서 질문과 답 후보를 만들고 그 snapshot을 다시 검색하는 실행은 `synthetic_self_retrieval_diagnostic`으로만 부릅니다. 이는 표현 변형과 pipeline 배관을 검사할 수 있지만 독립적인 인터넷-vs-local coverage 평가는 아닙니다. 승인된 별도 웹 passage를 사용하거나 source-document holdout을 적용하기 전에는 독립 기준답 실험이라고 주장하지 않습니다.

최종 verifier는 JSONL의 질의 ID 집합과 Markdown의 질의 ID 집합이 정확히 같고 1,000개 이상인지 검사합니다. 결과가 길면 여러 Markdown 파일로 분할하되 index에서 모든 파일과 건수를 연결합니다.

## 4시간 soak protocol

기능 smoke가 통과한 뒤에만 실행합니다.

- 정확한 runtime·model artifact·quantization·context·GPU layer·prompt·seed·DB snapshot hash를 manifest에 고정
- warm-up 결과를 측정에서 제외
- 고정 순서와 seeded random 순서를 별도 phase로 실행
- cache 상태와 반복 번호를 기록
- retrieval ranking 일치율, 구조화 출력 일치율, error/crash, latency p50/p95/max, throughput, RSS/VRAM peak와 증가량, 로그 크기, 관찰된 외부 연결을 수집
- 중단: OOM 1회, process crash 1회, 허용되지 않은 외부 연결 1회, 연속 timeout, 설정한 disk/log 상한 초과

시간을 채웠다는 사실만으로 합격하지 않습니다. threshold는 smoke 측정 후 freeze하며, 결과에는 `local bench only`를 표시합니다.

## 외부 전송 경계

| 구성 | 읽을 수 있는 데이터 | 전송 대상 | 허용 조건 |
|---|---|---|---|
| reference builder | 승인된 공개 질문과 공개 웹 자료 | 승인된 공개 도메인 | 사내 데이터 입력 금지, URL·passage·조회시각·revision·digest·patch 범위·파생 방법 기록 |
| local retriever | 승인된 로컬 corpus와 질문 | 없음 | outbound deny |
| local generator | top 3~5 로컬 chunk와 질문 | allowlist된 localhost 추론 endpoint만 | tool 없음, 동시성 1부터 측정 |
| public git push | 공개 코드·합성 fixture·검증 기록 | GitHub | scrub 검사 후 사람의 출간 지시 범위에서만 |

## 성공 증거

- schema validation과 단위 테스트
- SQLite integrity 및 snapshot SHA-256
- 설정·prompt·모델·quantization·seed의 실행 manifest
- 검색 후보와 인용 ID를 포함한 JSONL 결과
- 동일 설정 반복 결과와 시간·메모리·토큰 관측
- secret/local-path/company-term scrub
- public-source provenance와 reference-canary 누수 검사
- 변경별 auto-grill verdict와 rollback
