---
plan_version: 2
project: strategy-language-2-0
project_status: READY
current_phase: P0
current_pr: P0-01
active_prs: []
parallel_window: [P0-01]
last_updated: 2026-09-20T21:32:49+09:00
planned_prs: 27
merged_prs: 0
approved_prs: 0
progress_percent: 0
---

# 전략 언어 2.0 · 파이프라인 캔버스 실시간 진행 계획

상세 범위와 acceptance는 [WORKFLOW.md](./WORKFLOW.md), 계약은
[설계 spec](../../superpowers/specs/2026-09-20-strategy-language-2-0-and-pipeline-canvas-design.md)을 따른다.

## 자동 집계 상태

<!-- PLAN:SUMMARY:START -->
| Field | Value |
|---|---|
| Project status | `READY` |
| Current phase | `P0` |
| Current/next PR | `P0-01` |
| Active PR | none |
| Progress | `0 / 27 merged (0%)` |
| Approved | `0 / 27` |
| Aggregated at | `2026-09-20 21:32 KST` |
<!-- PLAN:SUMMARY:END -->

진척도는 PR tracker의 `[x]` 수를 기준으로 계산한다. frontmatter와 위 표, Phase 집계는
[update-plan-progress.ps1](./tools/update-plan-progress.ps1)이 생성하며 직접 수정하지 않는다.

## 현재 결정

- 2026-09-20 제품 소유자 결정: 템플릿·프리셋은 주 진입 경로가 아니다(튜토리얼 전용). 목표는 범용
  전략 구성이며, 언어 밖에서 정할 수 있는 것(시장·기간·유니버스·수수료·체결)은 실행 설정으로 뺀다.
- 언어 2.0 범위는 spec 대안 절의 (c)다: 실행 설정 분리 + `steps` 표기·배관 제거 + `filter`·`combine`·
  `select`·`weight` 재구성을 한 스키마 판으로. 1.0·1.1 revision은 동결 이력, 업그레이더 한 벌.
- 한글 어휘의 소유: 키(`x-description-key`, 연산자 카탈로그)는 backend, 문장은 frontend i18n. 적용
  조건 문장과 같은 규칙.
- 결합 정규화는 `combine.method`(기본 `rank_weighted`)가 소유한다. 1.1에서 올라온 문서는
  `raw_weighted`를 명시해 실행 의미를 보존한다.
- 그래프 라이브러리 도입은 P6-01 ADR이 결정한다. 레시피 빌더(P5)가 먼저 비전공자 경로를 닫는다.
- P2 backend PR은 `backend/openapi.json`만 재생성하고 frontend generated SDK는 P3-01이 갱신한다.
  P2 스택은 backend gate만 merge gate로 삼는다.
- P1 스택과 P2 스택은 독립이라 병렬 진행할 수 있다. P3-01은 P1-05·P2-07 둘 다 merge 뒤 시작한다.
- reviewer 서브에이전트는 Opus로만 배정한다. Phase 종료마다 SoT·책임분리 점검 서브에이전트를 돌린다.
- 완료 정의는 spec 5절의 6항이다. 특히 퀀트 아이디어 5개(12-1 모멘텀, 저PBR+고ROE, 20일 이평 돌파,
  거래대금 상위 20%, 변동성 역가중)가 파이프라인·팩터 탭만으로 백테스트에 도달해야 한다.

## 상태 값

| 상태 | 의미 |
|---|---|
| `PLANNED` | 범위만 정의됨 |
| `READY` | dependency가 충족되어 시작 가능 |
| `WAITING` | 선행 PR을 기다림 |
| `IN_PROGRESS` | 구현 중 |
| `SELF_CHECK` | 구현 완료, 작성자 검증 중 |
| `IN_REVIEW` | diff 고정, review sub-agent 검토 중 |
| `CHANGES_REQUESTED` | blocking finding 수정 중 |
| `APPROVED` | reviewer 승인, merge gate 확인 중 |
| `MERGED` | 로컬 gate 후 main merge 완료 |
| `PAUSED` | 제품·계약 결정이 필요해 일시 정지 |

기본 active PR은 하나다. `parallel_window`에 PR ID를 먼저 기록할 때만 최대 두 개를 허용한다(P1·P2
병렬은 이 규칙으로 운영한다).

## Phase 자동 집계

<!-- PLAN:PHASES:START -->
| Phase | Goal | PR | Merged | Status |
|---|---|---:|---:|---|
| P0 | Planning package and contract docs | 1 | 0 | `READY` |
| P1 | In-screen friction removal on 1.1 | 5 | 0 | `WAITING` |
| P2 | Backend schema 2.0 | 7 | 0 | `WAITING` |
| P3 | Frontend 2.0 adaptation | 3 | 0 | `WAITING` |
| P4 | Pipeline canvas | 4 | 0 | `WAITING` |
| P5 | Recipe builder | 4 | 0 | `WAITING` |
| P6 | Advanced node canvas | 3 | 0 | `WAITING` |
| **Total** |  | **27** | **0** | **0%** |
<!-- PLAN:PHASES:END -->

## 현재 작업 Packet

| 항목 | 값 |
|---|---|
| PR | `P0-01` |
| Intent | 기획 패키지·spec을 main에 올리고 ADR·로드맵·SoT가 이 initiative를 가리키게 한다 |
| Acceptance | WORKFLOW P0-01 |
| Non-goals | 코드 변경 |
| Branch/worktree | `docs/strategy-language-2-0-plan` |
| Base SHA | — |
| Head SHA | — |
| Diff stat | — |
| Focused tests | `tools/update-plan-progress.ps1 -Check` |
| Full gate | — |

---

## P0 — 기획 패키지와 계약 문서

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P0-01` | 기획 패키지·설계 spec·ADR/로드맵/SoT 개정 | 없음 | `READY` | — |

Phase exit:

- [ ] `update-plan-progress.ps1 -Check` 통과, 상위 문서 링크 확인.

## P1 — 화면 안에서 끝나는 마찰 제거

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P1-01` | 문제 목록·검증 배지를 탭과 무관하게 렌더 | P0-01 | `WAITING` | — |
| [ ] | `P1-02` | 되돌리기·다시 실행 버튼, 전역 단축키 | P1-01 | `WAITING` | — |
| [ ] | `P1-03` | 연산자 카탈로그(backend)·노드/필드 한글 이름·설명 | P1-02 | `WAITING` | — |
| [ ] | `P1-04` | 연산자 먼저 고르기(kind 자동), 조용한 실패 피드백, 오류 본문 인라인 | P1-03 | `WAITING` | — |
| [ ] | `P1-05` | 구조 오류 한글화, 진단 코드 네임스페이스, 순환·중복 진단에 node_id | P1-04 | `WAITING` | — |

Phase exit:

- [ ] e2e 그래프 시나리오가 YAML 탭 전환 없이 통과.
- [ ] 노드 property·kind·연산자 설명 커버리지 100%.
- [ ] SoT·책임분리 점검 서브에이전트 blocking 0.

## P2 — backend schema 2.0

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P2-01` | `RunEnvironment` 모델, 실행 요청 optional `environment`(브리지), manifest·캐시 키 | P0-01 | `WAITING` | — |
| [ ] | `P2-02` | StrategySpec 2.0 (1): `data`·`execution`·`label`·`missing_policy` 제거, 버전 2.0, fixture·hash golden | P2-01 | `WAITING` | — |
| [ ] | `P2-03` | StrategySpec 2.0 (2): `filter`·`combine`·`select`·`weight` 재구성, method union, 컴파일러 정규화 | P2-02 | `WAITING` | — |
| [ ] | `P2-04` | `steps` 표기 → `FactorGraph` 컴파일, steps·nodes 같은 hash, saved_* 제거 | P2-03 | `WAITING` | — |
| [ ] | `P2-05` | combine 단위 경고, boolean 승격, compile 단일 게이트, 연산자 unsupported | P2-04 | `WAITING` | — |
| [ ] | `P2-06` | filter 횡단면 규칙, `weight.by` 팩터 참조, group 정리, `ideas/*.yaml` 5개 | P2-05 | `WAITING` | — |
| [ ] | `P2-07` | 1.1 → 2.0 업그레이더(dict·source), upgrade 응답 `environment`, 동결 읽기, OpenAPI | P2-06 | `WAITING` | — |

Phase exit:

- [ ] 2.0 fixture 같은 hash, 1.1 fixture 전부 업그레이드 통과.
- [ ] compile 통과 문서가 preview에서 422 없음(property).
- [ ] `ideas/*.yaml` 5개 backend 통과.
- [ ] SoT·책임분리 점검 blocking 0, SoT 행 3개 채움.

## P3 — frontend 2.0 적응

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P3-01` | SDK 2.0, pointer 헬퍼·outline·snippet·Form projection(method union)·plan·debugger 적응 | P2-07, P1-05 | `WAITING` | — |
| [ ] | `P3-02` | 실행 설정 패널 확장, 1.1 업그레이드 배너(`environment` prefill), 실행 설정 띠 | P3-01 | `WAITING` | — |
| [ ] | `P3-03` | e2e fixture 2.0, 매뉴얼·README·FACTORS 2.0, CI green | P3-02 | `WAITING` | — |

Phase exit:

- [ ] CI 전체 green.
- [ ] 같은 전략·다른 기간 → 같은 spec_hash e2e.
- [ ] SoT·책임분리 점검 blocking 0.

## P4 — 파이프라인 캔버스

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P4-01` | `pipeline-projection.ts` (4단계 모델, 팩터 요약 문장) | P3-03 | `WAITING` | — |
| [ ] | `P4-02` | 단계 카드 UI(거른다·합쳐서 고른다·비중을 준다), 실행 설정 띠 | P4-01 | `WAITING` | — |
| [ ] | `P4-03` | 팩터 카드, 빈 팩터 추가, 기준일 미리보기 패널 | P4-02 | `WAITING` | — |
| [ ] | `P4-04` | IDE 탭 재편·기본 탭 파이프라인, 빈 화면 e2e, 식별자 0개 단언 | P4-03 | `WAITING` | — |

Phase exit:

- [ ] 빈 화면 e2e green, 식별자 0개 단언 green.
- [ ] SoT·책임분리 점검 blocking 0.

## P5 — 레시피 빌더

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P5-01` | `recipe-transactions.ts` (steps 추가·삭제·이동·파라미터·inputs), property test | P4-04 | `WAITING` | — |
| [ ] | `P5-02` | 팔레트(연산자 카탈로그), 단계 카드 UI, 설명, 인라인 진단 | P5-01 | `WAITING` | — |
| [ ] | `P5-03` | (backend) `POST /strategy-documents/expand-steps` | P5-02 | `WAITING` | — |
| [ ] | `P5-04` | 팩터 결과 미리보기·결측 표시·고급 전환, 아이디어 5개 e2e | P5-03 | `WAITING` | — |

Phase exit:

- [ ] 아이디어 5개 e2e green (완료 정의 1·2·4).
- [ ] SoT·책임분리 점검 blocking 0.

## P6 — 고급 노드 캔버스

| 완료 | PR | 결과물 | Dependency | 상태 | Review |
|---|---|---|---|---|---|
| [ ] | `P6-01` | 그래프 라이브러리 ADR·스파이크 | P5-04 | `WAITING` | — |
| [ ] | `P6-02` | 노드 캔버스: 드래그 배선, 좌표 local state, 자동 정렬, 키보드 | P6-01 | `WAITING` | — |
| [ ] | `P6-03` | 노드 위 진단, 복제, 마감 문서(SoT·로드맵 M8·매뉴얼) | P6-02 | `WAITING` | — |

Phase exit:

- [ ] 드래그 배선 → YAML 반영 → 되돌리기 e2e.
- [ ] SoT·책임분리 점검 blocking 0. 완료 정의 6항 전부 기록.

## Review 기록

| PR | Reviewer | 회차 | 결과 | 비고 |
|---|---|---|---|---|

## 검증 기록

| PR | 명령 | 결과 | 일시 |
|---|---|---|---|

## 변경 기록

- 2026-09-20 — 패키지 생성. 디자인보드 원인 분석(코드 감사 2건, 아이디어 5개 실험)과 제품 소유자
  결정(템플릿 배제, 실행 설정 분리, 언어 축소)을 spec D1~D12와 Phase 0~6, 27 PR로 정리.

## 갱신 절차

1. PR row의 상태·Review 열과 `현재 작업 Packet`을 고친다.
2. `변경 기록`에 한 줄 남긴다.
3. `pwsh docs/planning/strategy-language-2-0/tools/update-plan-progress.ps1`을 실행한다(`-Check`는 검증만).
