---
plan_version: 1
project: validation-lab
project_status: IN_PROGRESS
current_phase: V2,V3
current_pr: V2-01,V3-01
active_prs: [V2-01, V3-01]
parallel_window: [V2-01, V3-01]
last_updated: 2026-09-29T18:44:17+09:00
planned_prs: 28
merged_prs: 2
approved_prs: 2
progress_percent: 7
---

# 검증 랩 실시간 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 계약은
[설계 spec](../../superpowers/specs/2026-09-29-validation-lab-design.md)을 따른다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `IN_PROGRESS` |
| Current phase | `V2,V3` |
| Current/next PR | `V2-01,V3-01` |
| Active PR | `V2-01, V3-01` |
| Progress | `2 / 28 merged (7%)` |
| Approved | `2 / 28` |
| Aggregated at | `2026-09-29 18:44 KST` |
<!-- PLAN:SUMMARY:END -->

진척도는 PR tracker의 `[x]` 수를 기준으로 계산한다. frontmatter와 위 표, Phase 집계는
[update-plan-progress.ps1](./tools/update-plan-progress.ps1)이 생성하며 직접 수정하지 않는다.

## 현재 결정

- 2026-09-29 제품 소유자: 검증 랩 UI/UX 기획(디자인보드)을 승인하고 전체 앱 UX·lang2 정합 보강을 지시했다.
- 2026-09-29 제품 소유자: 전략 계열 = `strategy_id` + 계보 상속, 초기화 불가·합치기만(spec D2).
- 2026-09-29 제품 소유자: "시도 수가 굳이 안 올라가도 되는 경우엔 올리지 않게" — 새로 고를 거리가 생긴 실행만
  N에 센다(spec D2 시도 키 표).
- 2026-09-29 제품 소유자: 여러 실험을 비동기 대기열에 넣어 병렬로 돌린다(spec D6).
- 2026-09-29 제품 소유자: "다 하면 구현 시작해 lang2 들어가면 내가 말해줄게" — lang2와 겹치지 않는 backend를
  먼저 구현하고 화면(V5)은 lang2 머지 신호 뒤.
- 2026-09-29 제품 소유자 확인: lang2 통합 브랜치는 #202로 이미 main에 있고 남은 lang2는 IDE 가운데 영역만 바꾸므로
  화면을 lang2 신호로 묶지 않는다. 화면 PR은 각 화면의 backend PR 뒤에 시작하고, IDE에 붙는 V5-07만 lang2 P4-04·P5-02
  뒤다. #277은 이슈 큐 작업 흐름이 고치므로 V1-04에서 뺐다.
- 2026-09-29 리드: 조사로 드러난 미결 6건을 spec에 결정으로 적었다. 봉인 판정은 `require_environment` 관문
  (D1), 인라인 초안 계열은 `lineage_strategy_id`(D2), 파라미터는 해소값 배선(D4), 재시작한 단일 실행은
  `interrupted`로 닫고 실험 trial은 재대기(D3·D6), 스레드 모델 유지(D6), 팩터·equity 미리보기에도 잠금(D1).
- 2026-09-29 V0-01 리뷰(1차 REQUEST_CHANGES) 반영: PR ID를 lang2와 겹치지 않게 `V*`로 바꿨고, #161 브랜치가 US-SM-11을
  먼저 써서 이 initiative의 한상목 스토리를 US-SM-12~15로 옮겼다. 결과 공유·중복 실행 제거는 `run_fingerprint`,
  시도 키는 N 집계 전용(D6). `experiment_run`은 자기 outgoing port로 스케줄러를 받는다(새 화살표 없음). 시도 키는
  기본값 칸을 표기에서 빼 칸 추가에 흔들리지 않고, 제목·설명·파라미터 범위만 바꾼 저장은 새 시도가 아니다(D2).
  `environment_hash` 판본은 두지 않는다(D7).
- 2026-09-29 리드: #161·#274 브랜치는 다른 작업 흐름이 소유한다. V1-03·V4-01·V3-05는 그 머지 뒤다.

## 상태 값

`PLANNED` 계획만 있음 · `READY` 의존 PR 전부 머지 · `WAITING` 외부 선행 대기 · `IN_PROGRESS` 구현 중 ·
`SELF_CHECK` 게이트 중 · `IN_REVIEW` 리뷰 중 · `CHANGES_REQUESTED` · `APPROVED` · `MERGED` · `PAUSED`

## Phase 자동 집계

<!-- PLAN:PHASES:START -->
| Phase | Goal | PR | Merged | Status |
|---|---|---:|---:|---|
| V0 | Planning package | 1 | 1 | `MERGED` |
| V1 | Research window seal, run persistence, trial ledger | 5 | 1 | `WAITING` |
| V2 | Cost realism: sell tax, ADV participation, sqrt impact | 3 | 0 | `SELF_CHECK` |
| V3 | Experiment backend, async queue, walk-forward | 5 | 0 | `IN_PROGRESS` |
| V4 | Validation statistics: PSR, DSR, plateau, capacity, factor regression | 5 | 0 | `WAITING` |
| V5 | Screens (after lang2 merge signal) | 7 | 0 | `WAITING` |
| V6 | Holdout one-time opening | 2 | 0 | `WAITING` |
| **Total** |  | **28** | **2** | **7%** |
<!-- PLAN:PHASES:END -->

## V0 — 기획 패키지

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `V0-01` | 기획 패키지·설계 spec·유저 스토리 등록 | 없음 | `MERGED` | [#282](https://github.com/Nochiski/Quant_study/pull/282) · `review_vlab_p0_01` 3차 APPROVE(1차 REQUEST_CHANGES P1 5건 해소, 비blocking 전부 반영) · main 머지 `2178f73b`(2026-09-29) |

## V1 — 봉인과 기록

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `V1-01` | 연구 구간 잠금(실행·미리보기·추적) | V0-01 | `MERGED` | [#287](https://github.com/Nochiski/Quant_study/pull/287) · review_vlab_v1_01 APPROVE(P3 3건 반영) · main 머지 `69376124`(2026-09-29) |
| [ ] | `V1-02` | 연구 구간 잠금 확장(팩터·equity 미리보기) | V1-01 | `PLANNED` | — |
| [ ] | `V1-03` | 실행 기록 영속화(research DB, interrupted) | V0-01, #161 머지 | `WAITING` | — |
| [ ] | `V1-04` | 결과 재적재 | V1-03, #277 머지 | `PLANNED` | — |
| [ ] | `V1-05` | 시도 원장·시도 키 분류표·계열 합치기·봉인 원장 차단 기록 | V1-01, V1-03 | `PLANNED` | — |

## V2 — 비용 현실화

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `V2-01` | 매도 거래세(법정 세율표) | V0-01 | `SELF_CHECK` | — |
| [ ] | `V2-02` | ADV 배선·참여 기준(adv20) | V2-01 | `PLANNED` | — |
| [ ] | `V2-03` | √ 시장충격 모델 | V2-02 | `PLANNED` | — |

## V3 — 실험 backend

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `V3-01` | domain.experiment(SearchSpec·SplitSpec·trial 상태·이웃)·파라미터 허용값 술어(domain/strategy) | V0-01 | `IN_PROGRESS` | — |
| [ ] | `V3-02` | 파라미터 값 배선(parameter_values) | V3-01 | `PLANNED` | — |
| [ ] | `V3-03` | 실험 저장소·experiment_run·API | V3-02, V1-05 | `PLANNED` | — |
| [ ] | `V3-04` | 대기열 확장(슬롯·공정 분배·중복 제거·복구·일시정지) | V3-03 | `PLANNED` | — |
| [ ] | `V3-05` | 워크포워드 실행·이어 붙인 OOS·유지율 | V3-04, #274 머지 | `PLANNED` | — |

## V4 — 검증 통계

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `V4-01` | PSR 지표 | V0-01, #274 머지 | `WAITING` | — |
| [ ] | `V4-02` | DSR·선택 기록 | V4-01, V3-05 | `PLANNED` | — |
| [ ] | `V4-03` | 고원·민감도 | V4-02 | `PLANNED` | — |
| [ ] | `V4-04` | 용량 스윕 | V4-03, V2-03 | `PLANNED` | — |
| [ ] | `V4-05` | 팩터 회귀(시장·규모·가치·모멘텀, HAC) | V4-04 | `PLANNED` | — |

## V5 — 화면 (각 화면의 backend PR 뒤)

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `V5-01` | 실험 라우트·대기열·모니터·완료 알림 | V3-04 | `PLANNED` | — |
| [ ] | `V5-02` | 결과 검증 카드·튼튼한지 확인하기·AI 검증 요약 | V5-01, V3-05, V4-02, V4-03 | `PLANNED` | — |
| [ ] | `V5-03` | 백테스트 이력 종류 칼럼·전략 이력 원장 탭·계열 합치기 | V1-05 | `PLANNED` | — |
| [ ] | `V5-04` | 후보 탐색 히트맵·선택 | V5-01, V4-03 | `PLANNED` | — |
| [ ] | `V5-05` | 실행 설정 잠금 UX·시도 영향 알림 | V1-01, V1-05 | `PLANNED` | — |
| [ ] | `V5-06` | 용량 스윕·팩터 회귀 화면 | V5-02, V4-04, V4-05 | `PLANNED` | — |
| [ ] | `V5-07` | IDE 진입점·탐색 토글 | V5-02, V5-03, lang2 P4-04 머지, lang2 P5-02 머지 | `PLANNED` | — |

## V6 — 홀드아웃 개봉

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `V6-01` | 개봉 backend(사양 동결·기준 등록·개봉 원장) | V1-01, V1-05, V4-02 | `PLANNED` | — |
| [ ] | `V6-02` | 개봉 화면 | V6-01, V5-02 | `PLANNED` | — |

## 현재 작업 Packet

- V0-01: 이 패키지·spec·유저 스토리 등록. 브랜치 `docs/validation-lab-plan`, base main `f49e0afd`.

## Review 기록

- V0-01 `review_vlab_p0_01`: 1차 REQUEST_CHANGES(P1 5건: ID 충돌·스토리 번호 충돌·결과 공유 키·포트 방향·시도 키
  안정성) → 58875491 반영 → 2차 APPROVE(P2 1·P3 6) → 1a1f7aca 반영 → 2de1f968(화면 선행 개정) 3차 APPROVE(P2 1·P3 2)
  → 머지 전 반영.

## 변경 기록

- 2026-09-29: 패키지 생성(V0-01).

## 갱신 절차

PR 상태를 바꾼 뒤 `powershell -File docs/planning/validation-lab/tools/update-plan-progress.ps1`을 돌리고,
`-Check`로 집계가 최신인지 확인한다.
