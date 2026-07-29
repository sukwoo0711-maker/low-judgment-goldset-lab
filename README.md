# low-judgment-goldset-lab

사람이 정답 문장을 길게 작성하지 않고 `예/아니요/모름`만 선택해도, 복합·혼합언어·동의표현 질의 검색을 진단할 수 있는 로컬 평가 실험입니다.

이 저장소의 목적은 “좋은 챗봇”을 바로 만드는 것이 아닙니다. 질문 실패를 다음처럼 분리하는 것이 목적입니다.

```text
질문
  -> 로컬 corpus에 필요한 근거가 있는가?        C
  -> 검색 결과 top-k에 그 근거가 나타나는가?    R
  -> 필수 사실과 각 답변 주장이 근거에 맞는가?  A
  -> 인용이 유효한가?                           G
  -> 기권 판단이 타당한가?                      X
```

## 핵심 경계

- 인터넷 자료는 공개 평가 fixture를 만드는 별도 단계에서만 사용합니다.
- 웹에서 생성된 답변을 정답으로 간주하지 않습니다. 출처 문단과 필수 사실(predicate)을 동결하고 사람이 승인합니다.
- 로컬 QA 실행에는 질문만 전달합니다. 웹 답변·oracle 용어·평가 predicate는 전달하지 않습니다.
- 현재 검색기의 top-k만으로 `corpus에 답이 없다`고 판정하지 않습니다. 서로 다른 검색 경로로 만든 독립 evidence pool을 확인합니다.
- 사람 입력은 `Y/N/U`와 고정 사유 버튼으로 제한합니다. 도구가 predicate와 claim 후보를 제시하고, 사람은 문장을 작성하지 않습니다.
- 모델이 근거가 없으면 답을 만들지 않고 `abstain`해야 합니다.

## 왜 별도 저장소인가

기존 검색 저장소의 가중치·동의어·reranker 변경과 평가자료 생성을 분리해야 평가 누수를 추적할 수 있습니다. 이 저장소에는 공개 예제와 평가 하네스만 둡니다. 사내 사양서, 결함, 질의, 계정, 로컬 경로는 커밋하지 않습니다.

## 단계

1. 12개 독립 질문으로 실행·인용·기권·로그 배관을 검증합니다.
2. 30개 독립 질문으로 corpus/search/generation 실패 분포만 진단합니다.
3. 최종 실행은 최소 1,000개 질의를 사용합니다. 동일 사실의 표현 변형은 독립 표본처럼 세지 않고 `fact_cluster_id`로 묶습니다.
4. 4시간 이상 반복 실행하여 재현성, 지연, 메모리, 출력 변동과 외부 연결을 기록합니다.
5. 모든 질의의 원문, 로컬 검색 결과, 로컬 답변, 인터넷 reference answer를 사람이 읽을 수 있는 Markdown으로 생성합니다.

12개나 30개 결과로 BG3 전체 또는 사내 검색 품질을 주장하지 않습니다. 1,000개 표현이 소수 사실에서 파생되었다면 질의 수와 독립 사실군 수를 함께 보고합니다.

## 회사 PC 경계

목표 기준은 RAM 64GB, VRAM 8GB입니다. 로컬 개발 PC 결과는 설치 가능성이나 회사 PC 성능의 증거가 아닙니다. 회사 측에서는 모델 출처·라이선스·해시·반입 절차, 로컬 추론 런타임 허용 여부, outbound deny 정책을 별도로 확인해야 합니다.

로컬 QA 경로는 외부 네트워크와 외부 토큰을 사용하지 않는 구성을 목표로 합니다. 공개 reference fixture 제작 단계의 웹 접근은 별도 작업이며 사내 데이터를 입력하지 않습니다.

현재 상태: 평가 계약과 1,000건 pipeline의 첫 구현이 존재합니다. 실제 fixture와 장기 실행 결과는 아직 생성 전이며 별도 검증 단위로 공개합니다.

## 1,000건 실행 경로

경로에는 사내 자료가 아닌 승인된 공개 SQLite snapshot만 사용합니다. fixture 제작은 공개 원문을 읽는 reference branch이며, local QA에는 `questions.jsonl`만 전달합니다.

```powershell
$env:PYTHONPATH = "src"

# 승인된 공개 snapshot manifest가 DB hash·domain·license를 묶어야 실행됨
python -m goldset_lab.fixture_builder --db <public-bg3.db> `
  --source-manifest <approved-public-source.json> `
  --trusted-manifest-sha256 <operator-approved-sha256> `
  --fixtures .artifacts/reference/fixtures.jsonl `
  --questions .artifacts/local-input/questions.jsonl --target 1000

# 기준답 후보와 질의 자연성을 한 화면에 하나씩 Y/N/U로 승인
python -m goldset_lab.review `
  --fixtures .artifacts/reference/fixtures.jsonl `
  --events .artifacts/review/events.jsonl `
  --labels .artifacts/review/labels.jsonl `
  --manifest .artifacts/review/manifest.json `
  --approved-questions .artifacts/local-input/approved-questions.jsonl `
  --approved-fixtures .artifacts/reference/approved-fixtures.jsonl

# reference가 없는 질문 projection + 로컬 DB만 사용해 검색·답변
python -m goldset_lab.local_runner --db <public-bg3.db> `
  --questions .artifacts/local-input/approved-questions.jsonl `
  --review-manifest .artifacts/review/manifest.json `
  --results .runs/results.jsonl --manifest .runs/manifest.json

# QA 완료 후 reference를 join하여 1,000건 전체 Markdown 생성
python -m goldset_lab.report `
  --fixtures .artifacts/reference/approved-fixtures.jsonl `
  --results .runs/results.jsonl `
  --labels .artifacts/review/labels.jsonl `
  --review-manifest .artifacts/review/manifest.json `
  --out-dir reports/full-run
```

`fixture_builder`와 `local_runner`의 기본 추론 endpoint는 `127.0.0.1`이며 loopback 이외 주소는 거부합니다. 이것은 애플리케이션 수준 통제입니다. 외부 연결 0을 입증하려면 회사 PC의 outbound deny와 별도 연결 관측 증거가 필요합니다.

현재 `fixture_builder`는 공개 snapshot에서 질문을 만들고 같은 계열 snapshot을 다시 찾는 `synthetic_self_retrieval_diagnostic`입니다. 실제 별도 인터넷 source에서 얻은 독립 기준답으로 오해하면 안 됩니다. 최종 보고서는 사람이 `reference_supported=Y`로 승인한 경우에만 `INTERNET REFERENCE ANSWER`라고 표시하며, 그 전에는 `GENERATED REFERENCE CANDIDATE`라고 표시합니다.

12건 smoke에는 세 명령 모두 `--mode smoke`를 사용하고 builder에는 `--target 12`를 지정합니다. 32건 diagnostic은 `--mode diagnostic --target 32`, 전체 실행은 기본 `--mode full --target 1000`입니다. smoke와 diagnostic 결과에는 `quality_claim_prohibited`가 기록됩니다.

## 개발 검증

설치 없이 저장소 루트에서 다음 명령을 사용합니다.

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

설치형 CLI가 필요하면 격리된 virtual environment에서 `python -m pip install -e .`을 실행합니다. 현재 패키지는 런타임 외부 의존성이 없습니다.
