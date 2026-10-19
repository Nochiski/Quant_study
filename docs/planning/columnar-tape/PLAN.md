---
plan_version: 1
project: columnar-tape
project_status: IN_PROGRESS
current_phase: T0
current_pr: T0-01,T0-02
active_prs: [T0-01, T0-02]
parallel_window: [T0-01, T0-02]
last_updated: 2026-10-20T01:56:56+09:00
planned_prs: 26
merged_prs: 0
approved_prs: 0
progress_percent: 0
---

# tape 열 지향 계산 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 계약은
[설계 spec](../../superpowers/specs/2026-10-20-columnar-tape-design.md)을 따른다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `IN_PROGRESS` |
| Current phase | `T0` |
| Current/next PR | `T0-01,T0-02` |
| Active PR | `T0-01, T0-02` |
| Progress | `0 / 26 merged (0%)` |
| Approved | `0 / 26` |
| Aggregated at | `2026-10-20 01:56 KST` |
<!-- PLAN:SUMMARY:END -->

진척도는 PR tracker의 `[x]` 수로 센다. frontmatter와 위 표, Phase 집계는
[공용 progress 도구](../../../tools/quant_study_dev/plan_progress.py)가 만들며 직접 고치지 않는다
(`uv run --no-sync python -m quant_study_dev.plan_progress --profile columnar-tape`, 검사는 `--check`).

## 현재 결정

- 2026-10-20 사용자: 측정 결과로 성능 개선안을 기획·구현·리뷰하고 블로커가 없으면 머지까지 끝낸다(묻지 않는다). tape 열 지향화는
  시제품 전략의 연산만이 아니라 제품 전체로 넓힌다.
- 2026-10-20 리드: 비트 단위 동일을 택하고 평가 판본을 올리지 않는다(spec D1). 연속 계좌 평가가 판본 불일치를 거절하기 때문이다.
- 2026-10-20 리드: T0-02(상한 배분의 해시 seed 비결정성)를 run worker W1-02보다 먼저 머지한다.

## 상태 값

`PLANNED`, `READY`, `WAITING`, `IN_PROGRESS`, `SELF_CHECK`, `IN_REVIEW`, `CHANGES_REQUESTED`, `APPROVED`, `MERGED`, `PAUSED`

## Phase 자동 집계

<!-- PLAN:PHASES:START -->
| Phase | Goal | PR | Merged | Status |
|---|---|---:|---:|---|
| T0 | Planning package and prerequisite defect | 2 | 0 | `IN_PROGRESS` |
| T1 | Parity harness | 1 | 0 | `WAITING` |
| T2 | Columnar raw panel port | 3 | 0 | `WAITING` |
| T3 | Columnar factor evaluator | 6 | 0 | `WAITING` |
| T4 | Pipeline switch | 3 | 0 | `WAITING` |
| T5 | Columnar compiler | 4 | 0 | `WAITING` |
| T6 | Consumer migration | 3 | 0 | `WAITING` |
| T7 | Legacy path removal | 3 | 0 | `WAITING` |
| T9 | Closeout review | 1 | 0 | `WAITING` |
| **Total** |  | **26** | **0** | **0%** |
<!-- PLAN:PHASES:END -->

## T0 — 기획·선행 결함

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `T0-01` | 기획 패키지(spec·SEMANTICS·PLAN·WORKFLOW), progress 프로필 | 없음 | `IN_PROGRESS` | - |
| [ ] | `T0-02` | 상한 배분 결정성(DEFECT-CT-02) | 없음 | `IN_PROGRESS` | - |

## T1 — 대조 하네스

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `T1-01` | 대조 하네스 골격, `scripts/parity_tape.py` | T0-01, T0-02 | `PLANNED` | - |

## T2 — 열 원시 포트

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `T2-01` | `RawPanel` 포트, numpy 사용 규칙 | T1-01 | `PLANNED` | - |
| [ ] | `T2-02` | duckdb 열 로딩 GRID | T2-01 | `PLANNED` | - |
| [ ] | `T2-03` | duckdb 열 로딩 LATEST·법인 축 | T2-02 | `PLANNED` | - |

## T3 — 열 평가기

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `T3-01` | 열 평가기 골격 | T2-01 | `PLANNED` | - |
| [ ] | `T3-02` | 시계열 끝점·min/max | T3-01 | `PLANNED` | - |
| [ ] | `T3-03` | 정확 평균 커널 | T3-02 | `PLANNED` | - |
| [ ] | `T3-04` | 정확 pstdev 커널 | T3-03 | `PLANNED` | - |
| [ ] | `T3-05` | 남은 연산(cs·group·중앙값 채움) | T3-04 | `PLANNED` | - |
| [ ] | `T3-06` | trace를 노드 캐시에서 | T3-05 | `PLANNED` | - |

## T4 — 파이프라인 전환

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `T4-01` | 파이프라인 열 경로(`tape_engine`, 기본 object) | T2-03, T3-06 | `PLANNED` | - |
| [ ] | `T4-02` | 결과 지연 생성 | T4-01 | `PLANNED` | - |
| [ ] | `T4-03` | 기본값 columnar 전환, Tier 2·BENCHMARK | T4-02 | `PLANNED` | - |

## T5 — 열 컴파일러

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `T5-01` | 열 컴파일러 1단계 | T4-03 | `PLANNED` | - |
| [ ] | `T5-02` | 2차 적격·순위 | T5-01 | `PLANNED` | - |
| [ ] | `T5-03` | fold·객체 생성·연속 일정 공유 | T5-02 | `PLANNED` | - |
| [ ] | `T5-04` | 해시 고속화 | T5-03 | `PLANNED` | - |

## T6 — 소비자 이전

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `T6-01` | trace 서비스 열 읽기 | T4-03 | `PLANNED` | - |
| [ ] | `T6-02` | S03·연속 계좌 열 읽기 | T4-03 | `PLANNED` | - |
| [ ] | `T6-03` | factor-regression 입력(W4-01 뒤) | T4-03, W4-01 | `PLANNED` | - |

## T7 — 옛 경로 삭제

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `T7-01` | 객체 평가기 삭제·digest 고정·SoT 갱신 | T6-01, T6-02, T6-03 | `PLANNED` | - |
| [ ] | `T7-02` | 객체 컴파일러 삭제 | T5-04 | `PLANNED` | - |
| [ ] | `T7-03` | 어댑터 단일화 | T7-01 | `PLANNED` | - |

## T9 — 종료

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `T9-01` | 종료 리뷰 | T7-02, T7-03 | `PLANNED` | - |

## Review 기록

(없음)

## 변경 기록

- 2026-10-20: 기획 패키지 초안(설계 에이전트 조사 결과를 spec·SEMANTICS·WORKFLOW로 정리).
- 2026-10-20: #570 1차 리뷰 REQUEST_CHANGES(P2 3건·P3 11건) 반영 — CT-02 범위 확대, 안정 정렬, DECIMAL 규칙, 인증 경계, 3.11 기준 판, 계약 단일화, 의존·교차 기록.
