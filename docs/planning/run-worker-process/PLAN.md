---
plan_version: 1
project: run-worker-process
project_status: IN_REVIEW
current_phase: W2
current_pr: W2-02
active_prs: [W2-02]
parallel_window: []
last_updated: 2026-10-20T16:49:34+09:00
planned_prs: 10
merged_prs: 5
approved_prs: 5
progress_percent: 50
---

# run 별도 프로세스 실행 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 계약은
[설계 spec](../../superpowers/specs/2026-10-19-run-worker-process-design.md)을 따른다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `IN_REVIEW` |
| Current phase | `W2` |
| Current/next PR | `W2-02` |
| Active PR | `W2-02` |
| Progress | `5 / 10 merged (50%)` |
| Approved | `5 / 10` |
| Aggregated at | `2026-10-20 16:49 KST` |
<!-- PLAN:SUMMARY:END -->

진척도는 PR tracker의 `[x]` 수로 센다. frontmatter와 위 표, Phase 집계는
[공용 progress 도구](../../../tools/quant_study_dev/plan_progress.py)가 만들며 직접 고치지 않는다
(`uv run --no-sync python -m quant_study_dev.plan_progress --profile run-worker-process`, 검사는 `--check`).

## 현재 결정

- 2026-10-19 사용자: run을 별도 프로세스로 빼는 구체적인 스펙과 개발 계획을 쓴다. 성능 문제이므로 측정으로 답을 정한다.
- 2026-10-19 사용자 요청·리드 판단: 10년 구간 측정은 2016~2019 홀드아웃을 열게 되므로 하지 않는다. 최장 허용 구간
  2020-01-02~2026-09-18(약 6.7년)과 1년 구간으로 잰다(`holdout-window.md`).
- 2026-10-20 사용자: 측정을 근거로 성능 개선안을 기획·구현·리뷰하고 블로커가 없으면 머지까지 끝낸다(묻지 않는다). 이 지시로 검증 랩
  spec D6의 "스레드 모델 유지 · 프로세스 워커 non-goal" 개정을 승인한 것으로 본다. tape 열 지향화는 제품 전체로 넓혀 별도 기획으로 둔다.

## 상태 값

`PLANNED`, `READY`, `WAITING`, `IN_PROGRESS`, `SELF_CHECK`, `IN_REVIEW`, `CHANGES_REQUESTED`, `APPROVED`, `MERGED`, `PAUSED`

## Phase 자동 집계

<!-- PLAN:PHASES:START -->
| Phase | Goal | PR | Merged | Status |
|---|---|---:|---:|---|
| W0 | Planning package and benchmark | 1 | 1 | `MERGED` |
| W1 | Computation boundary and worker process | 3 | 3 | `MERGED` |
| W2 | Spare worker and memory guard | 2 | 1 | `IN_REVIEW` |
| W3 | Default switch and SoT revision | 1 | 0 | `WAITING` |
| W4 | Factor regression job | 1 | 0 | `WAITING` |
| W5 | Interactive path decision gate | 1 | 0 | `WAITING` |
| W9 | Closeout review | 1 | 0 | `WAITING` |
| **Total** |  | **10** | **5** | **50%** |
<!-- PLAN:PHASES:END -->

## W0 — 기획

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `W0-01` | 기획 패키지, `backend/scripts/bench_run_isolation.py`, progress 프로필 | 없음 | `MERGED` | #569 · 3차 APPROVE(1차 P2 7·2차 P2 1 해소) · main 머지 4cda11bf(2026-10-20) |

## W1 — 경계와 worker

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `W1-01` | 계산 경계 추출, `RunExecutionPort`, `run_thread` adapter(동작 불변) | W0-01 | `MERGED` | #572 · 2차 APPROVE(1차 P2 1건 해소) · main 머지 50a5c6ac(2026-10-20) |
| [x] | `W1-02` | `bootstrap/run_worker`, IPC v1, `run_worker_process` adapter(opt-in), Windows backend job | W1-01 | `MERGED` | #575 · APPROVE(P3 6건 중 4건 W1-03, 2건 W2-02) · main 머지 5ed8c88a(2026-10-20) |
| [x] | `W1-03` | 취소=종료(감독 스레드 종결), worker 고장, 부모 사망(Job Object), 판본 묶음 대조, e2e hold 게이트, 실패 코드 2개 | W1-02 | `MERGED` | #581 · 2차 APPROVE(1차 P3 6·2차 P3 1 반영, 기존 결함 DEFECT-581-01 수정) · main 머지 75167a1e(2026-10-20) |

## W2 — 지연과 메모리

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [x] | `W2-01` | 예비 worker 항상 1개, V-7 gate | W1-03 | `MERGED` | #591 · 3차 확인 APPROVE(1차 P2-1·2차 P2-1 해소) · main 머지 3414611b(2026-10-20) |
| [ ] | `W2-02` | 메모리 가드(도는 run 남은 예약분 차감, 최대 RSS 기록) | W2-01 | `IN_REVIEW` | - |

## W3 — 기본값

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `W3-01` | `process` 기본값, Windows backend job, SoT·README 개정, 최장 구간 실데이터 V-3 | W2-02 | `PLANNED` | - |

## W4 — 다른 백그라운드 계산

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `W4-01` | factor-regression 계산의 worker 이전 | W3-01 | `PLANNED` | - |

## W5 — 결정 게이트

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `W5-00` | 대화형 무거운 경로 측정과 결정 기록 | W4-01 | `PLANNED` | - |

## W9 — 종료

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `W9-01` | 도메인 전체·경계 책임·SoT 종료 리뷰 | W5-00 | `PLANNED` | - |

## Review 기록

(없음)

## 변경 기록

- 2026-10-19: 기획 패키지 초안. 측정 근거는 [BENCHMARK.md](./BENCHMARK.md).
- 2026-10-20: #569 1차 리뷰 REQUEST_CHANGES(P2 7건·P3 15건) 반영 — D5 종결 순서, D6 판본 묶음, D7 예비 1개 상시, D8 남은 예약분 차감, D11 엔진 게이트, D12 잠금 규율, D13 port 계약, Windows job을 W1-02로.
- 2026-10-20: W1-03(#581) main 머지. W2-01 — 프로토콜 v2(`boot` 줄), 예비 worker 늘 1개(구현 세부는 spec D7), 서버 종료 정리의 lifespan 이전(#581 리뷰 nit).
- 2026-10-20: W2-01(#591) main 머지. W2-02 — 메모리 가드(프로세스 방식, 구현 세부는 spec D8), 프로토콜 v3(`ready` 에 인터프리터 pid), 최대 RSS 읽기를 메모리 모듈로 모음(#575 리뷰 P3).
