# 재부팅 후 재개 절차

GPU 하드웨어 장애(`GPU is lost. Reboot the system to recover this GPU`)로 로컬 추론이 중단되어 PC를 재부팅했습니다. 이 문서는 재부팅 이후 새 세션이 즉시 이어받기 위한 인계서입니다.

## 확정된 운영 결정

- **경로는 축소판입니다.** `reports/preflight-002/SUMMARY.md`가 정한 사전관문 4단계(수율 재측정 → 32건 diagnostic → 3~5분 soak → 재auto-grill 후 결정)는 **수행하지 않습니다.** 짧은 동작 확인만 하고 곧장 1,000질의 생성과 4시간 soak에 들어갑니다.
- 근거: 사전관문 4단계는 원 계약(`README.md` "단계")에 없던 항목이며, GPU 사망 직후 추가된 것입니다. 본실행 착수 지연이 더 큰 비용이라고 판단했습니다.
- 대가: 수율이 부족하면 본실행 도중 실패할 수 있습니다. 그때는 중단하고 수율 문제로 되돌아옵니다.

## 0단계 — GPU 복구 확인 (실패하면 여기서 멈출 것)

```powershell
nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv
```

`GPU is lost` 또는 `No devices were found`가 다시 나오면 하드웨어 문제이므로 진행하지 않습니다.

```powershell
# Ollama 기동과 GPU 적재 확인
Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/ps' -TimeoutSec 8
```

모델 적재 후 `size_vram`이 0이면 CPU fallback입니다. 이 상태의 성능 표본은 계약상 폐기해야 하므로 진행하지 않습니다.

## 1단계 — 회귀 확인 (GPU 불필요, 1분 이내)

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
python -m compileall -q src tests
```

기준: **60개 통과**. 외부 패키지 없이 stdlib만 씁니다.

## 2단계 — 짧은 동작 확인

기존 smoke 경로로 소수 청크만 생성해 추론 배관이 살아 있는지 봅니다. 수율 측정이 목적이 아니므로 결과를 gate로 쓰지 않습니다. 오류 없이 후보가 나오면 통과입니다.

## 3단계 — 1,000질의 본실행

목표는 질의 1,000건 이상입니다. 두 경로가 있고 **A를 권합니다.**

**경로 A (권장, 지금 바로 가능)** — 기존 모델 생성 4종 그대로. 클러스터당 4질의이므로 **250 클러스터**가 필요합니다. 관측 유효율 19.5%(251시도 중 49) 기준으로 대략 **1,300회 전후의 청크 시도**가 필요합니다. 이 시도량이 4시간 요구를 상당 부분 자연스럽게 채웁니다.

**경로 B (더 적은 추론, 단 계약 개정 선행)** — 결정론적 변형(`query_variants`)으로 4.33배 확장하면 자연질의 231건이면 충분합니다. 그러나 아래 게이트 셋이 확장 세트를 거부하므로 **개정 전에는 쓸 수 없습니다.**

| 게이트 | 위치 | 문제 |
|---|---|---|
| 사례 수 4의 배수 | `contracts.validate_mode_count` | 확장 세트는 질의당 변형 수가 달라 4의 배수가 아님 |
| 클러스터당 질의 정확히 4개 | `review.py` 승인 조건 | 확장 클러스터는 5개 이상 |
| 모든 질의에 사람 `Y` | `report.py` full gate | 파생 행에는 사람 판정이 없음 |

## 4단계 — 4시간 soak

`soak_runner`로 실행합니다. 계약(`docs/CONTRACT.md` "4시간 soak protocol")대로 manifest에 runtime·모델·양자화·context·GPU layer·prompt·seed·DB snapshot 해시를 고정하고, warm-up을 측정에서 제외하며, 고정 순서와 seeded random 순서를 별도 phase로 돌립니다.

중단 조건: OOM 1회, process crash 1회, 허용되지 않은 외부 연결 1회, 연속 timeout, disk/log 상한 초과. 실제 경과가 14,400초 미만이면 실패입니다.

## 알려진 결함 — 재개 시 밟지 말 것

1. **순수 띄어쓰기 변형은 통과할 수 없습니다.** `duplicate_query_text`가 네 질의를 공백 제거 후 비교하므로, 단어를 그대로 두고 띄어쓰기만 바꾼 질의는 항상 중복으로 폐기됩니다. 모델이 이 축을 못 만드는 것이 아니라 게이트가 막고 있습니다. 상세는 `reports/yield-attribution-002/SUMMARY.md`.
2. **`spacing_has_no_marker`의 `\bBG3\b` 분기는 죽은 코드입니다.** `[A-Z]{2,}`가 이미 `BG3`의 `BG`를 매치합니다.
3. **용어 사상표는 미승인 상태입니다**(`draft_unverified`). 로더가 채점 실행을 거부합니다. 승인 없이 쓰려면 `allow_unapproved`가 필요하고 그 결과는 채점 불가입니다.
4. **`query_variants`는 어떤 실행 경로에도 연결되어 있지 않습니다.** 위 3단계 경로 B 참조.
5. CPU fallback 상태에서 나온 성능 표본은 전부 폐기 대상입니다. GPU loss 이전 v3 실행분이 여기 해당합니다.

## 현재 상태

| 항목 | 값 |
|---|---|
| 최신 커밋 | `6b5a38c` |
| 테스트 | 60개 통과, stdlib `unittest`, GPU 불필요 |
| 1,000질의 실행 | **0건** |
| 4시간 soak | **0초** |
| 결정 로그 | `docs/DECISIONS.md` D001~D007 |

`.artifacts/`는 gitignore 대상이며 재부팅으로 사라지지 않습니다. 중단된 v1/v3 후보 생성 산출물이 남아 있으므로 재사용 전에 fingerprint를 확인하십시오.

## 작업 규약

- 한 단락이 확정될 때마다 auto-grill 후 push합니다.
- 사람이 읽는 문서는 한국어, 코드·커밋 메시지·식별자는 영어로 씁니다.
- 사내 데이터와 로컬 경로는 공개 저장소에 넣지 않습니다.
