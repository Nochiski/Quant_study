---
plan_version: 1
project: validation-lab
project_status: IN_PROGRESS
current_phase: P0
current_pr: P0-01
active_prs: [P0-01]
parallel_window: []
last_updated: 2026-09-29T15:27:04+09:00
planned_prs: 28
merged_prs: 0
approved_prs: 0
progress_percent: 0
---

# 검증 랩 실시간 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 계약은
[설계 spec](../../superpowers/specs/2026-09-29-validation-lab-design.md)을 따른다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `IN_PROGRESS` |
| Current phase | `P0` |
| Current/next PR | `P0-01` |
| Active PR | `P0-01` |
| Progress | `0 / 28 merged (0%)` |
| Approved | `0 / 28` |
| Aggregated at | `2026-09-29 15:27 KST` |
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
  먼저 구현하고 화면(P5)은 lang2 머지 신호 뒤.
- 2026-09-29 리드: 조사로 드러난 미결 6건을 spec에 결정으로 적었다. 봉인 판정은 `require_environment` 관문
  (D1), 인라인 초안 계열은 `lineage_strategy_id`(D2), 파라미터는 해소값 배선(D4), 재시작한 단일 실행은
  `interrupted`로 닫고 실험 trial은 재대기(D3·D6), 스레드 모델 유지(D6), 팩터·equity 미리보기에도 잠금(D1).
- 2026-09-29 리드: #161·#274 브랜치는 다른 작업 흐름이 소유한다. P1-03·P4-01은 그 머지 뒤다.

## 상태 값

`PLANNED` 계획만 있음 · `READY` 의존 PR 전부 머지 · `WAITING` 외부 선행 대기 · `IN_PROGRESS` 구현 중 ·
`SELF_CHECK` 게이트 중 · `IN_REVIEW` 리뷰 중 · `CHANGES_REQUESTED` · `APPROVED` · `MERGED` · `PAUSED`

## Phase 자동 집계

<!-- PLAN:PHASES:START -->
| Phase | Goal | PR | Merged | Status |
|---|---|---:|---:|---|
| P0 | Planning package | 1 | 0 | `IN_PROGRESS` |
| P1 | Research window seal, run persistence, trial ledger | 5 | 0 | `WAITING` |
| P2 | Cost realism: sell tax, ADV participation, sqrt impact | 3 | 0 | `WAITING` |
| P3 | Experiment backend, async queue, walk-forward | 5 | 0 | `WAITING` |
| P4 | Validation statistics: PSR, DSR, plateau, capacity, factor regression | 5 | 0 | `WAITING` |
| P5 | Screens (after lang2 merge signal) | 7 | 0 | `WAITING` |
| P6 | Holdout one-time opening | 2 | 0 | `WAITING` |
| **Total** |  | **28** | **0** | **0%** |
<!-- PLAN:PHASES:END -->

## P0 — 기획 패키지

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P0-01` | 기획 패키지·설계 spec·유저 스토리 등록 | 없음 | `IN_PROGRESS` | — |

## P1 — 봉인과 기록

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P1-01` | 연구 구간 잠금(실행·미리보기·추적·metric window) | P0-01 | `PLANNED` | — |
| [ ] | `P1-02` | 연구 구간 잠금 확장(팩터·equity 미리보기) | P1-01 | `PLANNED` | — |
| [ ] | `P1-03` | 실행 기록 영속화(research DB, interrupted) | P0-01, #161 머지 | `PLANNED` | — |
| [ ] | `P1-04` | 결과 재적재·상대 artifact 키(#277) | P1-03 | `PLANNED` | — |
| [ ] | `P1-05` | 시도 원장·시도 키 분류표·계열 합치기·봉인 원장 차단 기록 | P1-01, P1-03 | `PLANNED` | — |

## P2 — 비용 현실화

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P2-01` | 매도 거래세(법정 세율표)·environment_hash 판본 | P0-01 | `PLANNED` | — |
| [ ] | `P2-02` | ADV 배선·참여 기준(adv20) | P2-01 | `PLANNED` | — |
| [ ] | `P2-03` | √ 시장충격 모델 | P2-02 | `PLANNED` | — |

## P3 — 실험 backend

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P3-01` | domain.experiment(SearchSpec·SplitSpec·trial 상태·이웃) | P0-01 | `PLANNED` | — |
| [ ] | `P3-02` | 파라미터 값 배선(parameter_values) | P3-01 | `PLANNED` | — |
| [ ] | `P3-03` | 실험 저장소·experiment_run·API | P3-02, P1-05 | `PLANNED` | — |
| [ ] | `P3-04` | 대기열 확장(슬롯·공정 분배·중복 제거·복구·일시정지) | P3-03 | `PLANNED` | — |
| [ ] | `P3-05` | 워크포워드 실행·이어 붙인 OOS·유지율 | P3-04 | `PLANNED` | — |

## P4 — 검증 통계

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P4-01` | PSR 지표 | P0-01, #274 머지 | `PLANNED` | — |
| [ ] | `P4-02` | DSR·선택 기록 | P4-01, P3-05 | `PLANNED` | — |
| [ ] | `P4-03` | 고원·민감도 | P4-02 | `PLANNED` | — |
| [ ] | `P4-04` | 용량 스윕 | P4-03, P2-03 | `PLANNED` | — |
| [ ] | `P4-05` | 팩터 회귀(시장·규모·가치·모멘텀, HAC) | P4-04 | `PLANNED` | — |

## P5 — 화면 (lang2 머지 신호 뒤)

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P5-01` | 실험 라우트·대기열·모니터·완료 알림 | P3-05 | `PLANNED` | — |
| [ ] | `P5-02` | 결과 검증 카드·튼튼한지 확인하기·AI 검증 요약 | P5-01, P4-05 | `PLANNED` | — |
| [ ] | `P5-03` | 백테스트 이력 종류 칼럼·전략 이력 원장 탭·계열 합치기 | P5-02 | `PLANNED` | — |
| [ ] | `P5-04` | 후보 탐색 히트맵·선택 | P5-03 | `PLANNED` | — |
| [ ] | `P5-05` | 실행 설정 잠금 UX·비용 어휘·시도 영향 알림 | P5-04 | `PLANNED` | — |
| [ ] | `P5-06` | 용량 스윕·팩터 회귀 화면 | P5-05 | `PLANNED` | — |
| [ ] | `P5-07` | IDE 진입점·탐색 토글(lang2 P4-04·P5-02 뒤) | P5-06 | `PLANNED` | — |

## P6 — 홀드아웃 개봉

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P6-01` | 개봉 backend(사양 동결·기준 등록·개봉 원장) | P5-07 | `PLANNED` | — |
| [ ] | `P6-02` | 개봉 화면 | P6-01 | `PLANNED` | — |

## 현재 작업 Packet

- P0-01: 이 패키지·spec·유저 스토리 등록. 브랜치 `docs/validation-lab-plan`, base main `f49e0afd`.

## Review 기록

아직 없다.

## 변경 기록

- 2026-09-29: 패키지 생성(P0-01).

## 갱신 절차

PR 상태를 바꾼 뒤 `powershell -File docs/planning/validation-lab/tools/update-plan-progress.ps1`을 돌리고,
`-Check`로 집계가 최신인지 확인한다.
