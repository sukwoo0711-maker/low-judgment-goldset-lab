# Full-run 준비 preflight 002

## 결론

실제 1,000질의와 4시간 실행은 아직 완료되지 않았습니다. 이번 단락에서 확인한 것은 후보 생성 수율, 독립 웹 reference 한 건, 짧은 soak와 fail-closed 보강입니다.

## 후보 생성 실측

| 방식 | 관측 | 판정 |
|---|---:|---|
| 청크당 단일 사실 | 200청크에서 39사실군·156질의 | 1,400질의에 필요한 수율보다 낮아 중단 |
| 한 JSON에서 두 사실 | 20청크에서 1사실군·4질의 | Qwen2.5 7B 구조 준수 실패로 폐기 |
| 단일 사실 형식을 청크당 두 번 호출 | v3 진행 중 | 최소 100~200청크 전에는 수율 결론 금지 |

중단된 실행의 산출물은 private `.artifacts`에 보존했으며 공개 저장소에는 PID, 로컬 경로, 원시 cache를 넣지 않습니다.

## 독립 인터넷 reference smoke

- 입력: 3개 공개 BG3 사실군
- 별도 출처: [`bg3.wiki` MediaWiki API](https://bg3.wiki/w/api.php)
- 성공: 1개 사실군
- unsupported: 2개 사실군
- 성공 사례: d20에서 natural 20과 natural 1의 의미
- 검색 설계: 공개 reference 후보를 oracle search hint로만 사용하며 local QA 입력에는 전달하지 않음
- 라이선스: source case별 기록; [`bg3.wiki` Copyrights](https://bg3.wiki/wiki/bg3wiki:Copyrights)에 따라 CC BY-NC-SA 4.0 또는 CC BY-SA 4.0 및 페이지별 예외 확인 필요

이 결과는 인터넷 RAG 경로가 실제 공개 API에서 한 건 작동했다는 증거입니다. 12질의 전체 또는 1,000질의의 독립 기준답 coverage 증거가 아닙니다.

## 짧은 soak

| 항목 | 관측 |
|---|---:|
| 실제 시간 | 69.312초 |
| iteration | 1 |
| 오류 | 0 |
| GPU memory 관측 범위 | 6,175~6,196 MiB |
| 네트워크 관측 | 수행하지 않음 |
| company-PC 주장 | 금지 |

이 실행은 `short_test=true`이며 4시간 soak를 대신하지 않습니다. 후보 생성과 동시에 실행했을 때 latency가 크게 늘어 공용 local-model lock을 추가했습니다.

## 추가된 fail-closed 경계

- 자동 triage는 deterministic 또는 두 seed consensus의 N만 prelabel하고 자동 Y를 만들지 않음
- prelabel journal·model artifact·prompt·seed·fixture hash를 manifest에 결속
- 독립 web reference는 operator-approved public fixture receipt가 없으면 네트워크 호출 전 종료
- 독립 reference 병합 후 기존 review를 폐기하고 enriched fixture를 새로 검토
- full report는 모든 사실군의 different-domain reference, fresh human Y, labels hash, trusted receipt를 요구
- run별 exclusive lock과 공용 local-model lock 추가
- local QA의 누락 질문은 성공 종료하지 않음
- formal soak가 실제 14,400초 미만이면 실패

## 다음 gate

1. v3에서 최소 100~200청크 수율을 측정합니다.
2. 독립 웹 reference 성공률을 32질의 diagnostic에서 측정합니다.
3. 3~5분 다중 iteration soak에서 fixed/varied phase를 모두 실행합니다.
4. 위 결과를 다시 auto-grill한 뒤 실제 1,000질의와 4시간 실행 여부를 결정합니다.
