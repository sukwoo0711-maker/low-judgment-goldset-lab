# 결정 기록

## D001 — 평가 branch와 로컬 QA branch 분리

판정: `accept after revision`

웹 검색 결과가 로컬 검색 질의나 동의어에 유입되면 평가 누수가 생깁니다. 공개 reference는 fixture 제작에만 사용하고, 로컬 QA에는 자연 질의만 전달합니다. 별도 입력 root와 hash allowlist를 사용하고 canary token으로 누수를 자동 검사합니다.

## D002 — 사람 입력을 Y/N/U로 제한

판정: `accept after revision`

질문 전체를 한 번에 채점하지 않고 predicate·claim·인용·기권을 원자 단위로 순차 판정합니다. 도구가 predicate와 claim 후보를 만들고 사람은 `Y/N/U`와 고정 사유만 고릅니다. 선행 판정에 따라 불필요한 질문을 생략하고 Undo와 판정 시간 측정을 제공합니다.

## D003 — 기존 검색 결과로 goldset 자동 생성

판정: `reject as oracle`

현재 검색기가 찾은 문서를 그대로 expected로 삼으면 그 검색기의 어휘와 실패를 정답에 복제합니다. 자동 채굴 결과는 lexical·dense·entity·oracle·random/hard-negative pool 중 하나의 후보로만 사용하며 provenance를 숨긴 검토와 held-out 분리를 요구합니다.
