# YAML Strategy Workbench 구현 워크플로우

## 1. 목적

기존 Strategy Workbench를 전문 트레이더가 YAML 또는 JSON으로 전략을 작성하고, 계약을 확인하며, 실제 계산 중간값을 추적할 수 있는 StrategySpec 문서 IDE로 전환한다.

구현 기준 이미지는 [verbose YAML v3 시안](./assets/strategy-workbench-yaml-ui-concept-v3.png)이다. v3는 Contract Inspector와 debugger selector를 RFC 6901 JSON Pointer로 통일한다. [v2 시안](./assets/strategy-workbench-yaml-ui-concept-v2.png)과 [최초 시안](./assets/strategy-workbench-yaml-ui-concept.png)은 변경 기록으로만 보존한다. 실시간 진행 상태는 [PLAN.md](./PLAN.md)에서 관리한다.

## 2. 핵심 결정

### 2.1 전략 의미의 정본

YAML과 JSON은 사람이 작성하는 authoring source이고, 실행 의미의 정본은 기존 backend immutable/versioned `StrategySpec`이다.

```text
YAML 또는 JSON 원문
        │
        ▼
안전한 parse + source map
        │
        ├─ syntax/structure 오류 → 위치 표시, 저장·실행 차단
        ▼
StrategySpec
        │
        ├─ backend semantic validation
        ├─ canonical JSON
        ├─ spec_hash
        └─ execution plan
        │
        ▼
Preview / Backtest / Debug Trace
```

Form과 Graph는 첫 버전에서 별도 전략 편집 모델을 만들지 않고 현재 StrategySpec을 읽는 projection으로 제공한다.

### 2.2 v1 authoring 문법

v1은 canonical field name과 raw value를 그대로 쓰는 verbose YAML/JSON으로 확정한다. 시각적으로 간단해 보이기 위한 별도 문자열 DSL이나 단위 sugar를 도입하지 않는다.

```yaml
schema_version: "1.0"
title: "퀄리티 모멘텀"
description: ""
data:
  market: KRX
  start: "2021-01-01"
  end: "2026-08-31"
  universe_id: krx.common-stock
  frequency: daily
eligibility:
  rules: []
factors:
  factors:
    - factor_id: momentum
      label: "모멘텀"
      direction: high
      weight: 0.6
      graph:
        nodes:
          - node_id: close
            field_id: price.close
            kind: field
          - node_id: mom_252
            operator: momentum
            input_node_id: close
            window: 252
            kind: time_series
        output_node_id: mom_252
        missing_policy: drop
signal:
  method: weighted_sum
portfolio:
  selection_count: 20
  rebalance: monthly
risk:
  max_name_weight: 0.05
execution:
  timing: next_open
  fee_bps: 15.0
parameters: []
```

- `max_name_weight: 5%` 대신 `max_name_weight: 0.05`를 저장한다.
- `fee: 15bps` 대신 `fee_bps: 15.0`을 저장한다.
- `rebalance: month_end` 대신 실제 enum인 `rebalance: monthly`를 저장한다.
- `rank(neutralize(...))` 같은 표현식 문자열 대신 `graph.nodes[]`를 저장한다.
- `%`, `bps` 같은 사람이 읽기 좋은 단위는 Contract Inspector가 backend contract metadata로 표시한다.
- 문자열 DSL은 기존 roadmap의 custom formula editor에 해당하는 별도 후속 initiative다. 도입하려면 parser, 문자열 내부 source map, DSL→FactorGraph compiler, pretty-printer와 round-trip 검증이 선행되어야 한다.

### 2.3 제품 방향과 기존 no-code UI

전문 트레이더를 위한 YAML-first authoring을 기본 경로로 전환한다. 기존의 전역 완료 정의인 “코드를 몰라도 전략을 만들 수 있다”는 다음처럼 범위를 조정한다.

- 전략 정의의 primary authoring은 YAML/JSON이다.
- parameter search와 실험 실행은 source를 다시 편집하지 않고 UI에서 수행할 수 있어야 한다.
- Form/Graph v1은 projection이며 새로운 편집 SoT가 아니다.
- Quick/Advanced UI는 P0-01에서 deprecation 정책을 명시하고, migration acceptance가 끝나기 전에는 삭제하지 않는다.
- P0-01은 roadmap M6~M10의 순서와 완료 정의, `strategy-workbench-sot.md`, `frontend-testing.md`, README를 함께 갱신한다. i18n 문구는 legacy 편집기가 기본 화면인 동안 유지하고 P3-05 cutover에서 ko/en을 갱신한다.
- 상위 제품 milestone의 SoT는 기존 roadmap이다. 이 문서와 `PLAN.md`는 본 initiative의 구현 범위와 PR 상태만 소유하며, roadmap은 상세 체크리스트를 복제하지 않고 `PLAN.md`를 링크한다.

### 2.4 Revision 저장 단위

```text
StrategyRevision
├─ strategy_id / revision
├─ schema_version
├─ immutable StrategySpec
├─ spec_hash
├─ original source
│  ├─ format: yaml | json
│  ├─ exact text
│  └─ source_hash
└─ created_at / change_note
```

- 주석, 공백, key order 변화는 `source_hash`에만 반영한다.
- 전략 의미가 같다면 YAML과 JSON의 `spec_hash`는 같아야 한다.
- identity와 revision은 source 문서 밖의 revision envelope가 소유한다.
- canonical hash는 backend 응답만 신뢰한다.
- broker credential은 문서에 넣지 않고 secret reference만 허용한다.

### 2.5 실행 안전 불변식

현재 source가 아래 조건을 모두 만족할 때만 Save, Backtest, Debug를 허용한다.

```text
sourceVersion == compiledVersion
AND syntax blocking error == 0
AND structural blocking error == 0
AND semantic blocking error == 0
```

`lastValidSpec`은 stale 상태임을 명시하여 조회에만 사용할 수 있다. 현재 source가 invalid 또는 stale이면 과거 spec을 몰래 실행하지 않는다.

### 2.6 상태 소유권

| 상태 | 소유자 |
|---|---|
| YAML/JSON source, undo history, parse 상태 | `features/edit-strategy` |
| 저장 전략, revision, schema, preview, trace | React Query cache |
| 선택 view/path/date/security | URL search (TanStack Router `validateSearch`, P0-04 ADR) |
| 패널 크기 | workbench widget local state |
| theme, panel preference, 장애 복구본 | local persistence |
| canonical JSON/hash, semantic validation, 계산값 | backend |

## 3. 구현 순서

```text
Phase 0
   ├──────────────→ Phase 1 Backend Contract
   ├──────────────→ Phase 1.5 Backtest Correctness
   └──────────────→ Phase 2 App Shell
                         │
Phase 1 + Phase 1.5 + Phase 2
                    ────→ Phase 3 YAML MVP
                              │
                              ├→ Phase 4 IDE Projections
                              └→ Phase 5 Truthful Debugger
                                       │
                                       ▼
                                  Phase 6 Release
```

P0-01 이후 P1-01과 Phase 1.5의 독립 PR을 먼저 병행할 수 있다. 기본은 하나의 active PR이며, 병렬 window를 `PLAN.md`에 명시하고 별도 git worktree를 사용할 때만 최대 두 개를 허용한다. OpenAPI/generated SDK, domain contract, global CSS, editor state 또는 동일 fixture를 공유하면 순차 통합한다.

---

## 4. Phase 0 — 계약과 기술 선택

목표는 구현 도중 source와 StrategySpec의 역할 또는 editor 기술 선택이 바뀌지 않도록 결정하는 것이다.

### P0-01 — Strategy Authoring Contract ADR

범위:

- v1을 canonical verbose YAML/JSON으로 확정하고 DSL·단위 sugar를 non-goal로 명시
- YAML/JSON source와 StrategySpec 역할 정의
- revision envelope, schema version, source/spec hash 정의
- invalid/stale 상태의 Save·Run·Debug 차단 규칙
- Form/Graph의 초기 read-only 원칙
- YAML/JSON 명시적 변환 정책
- credential 금지 및 secret reference 원칙
- YAML, JSON, invalid YAML, legacy JSON acceptance fixture
- YAML-first 전문 사용자 제품 전환과 Quick/Advanced deprecation 정책
- roadmap M6~M10과 본 initiative의 선후 관계
- roadmap을 product milestone SoT, `PLAN.md`를 initiative delivery SoT로 지정
- `strategy-workbench-sot.md`, `frontend-testing.md`, README의 충돌 규칙 갱신 (i18n 문구는 P3-05)

완료 조건:

- 같은 의미의 YAML/JSON이 같은 canonical hash를 생성한다.
- 2.2의 완전한 YAML 예시가 identity 주입 후 StrategySpec으로 hydrate되며 이후 P1-03 golden compile fixture가 된다.
- identity 외 canonical round-trip이 보존된다.
- 기존 hash 알고리즘은 변경하지 않는다.
- no-code 관련 기존 완료 정의가 새로운 제품 방향과 모순되지 않는다.
- Quick/Advanced 삭제 조건과 source↔StrategySpec↔projection round-trip test가 명시된다.
- roadmap이 `PLAN.md`를 링크하고 PR 진행 상태를 중복 추적하지 않는다.
- 기능·비기능 non-goal이 명시되어 있다.

### P0-02 — Frontend editor 기술 스파이크

Monaco와 CodeMirror 6을 실제 fixture로 비교한다. IME 안정성과 lazy bundle 비용을 우선 기준으로 두고, schema completion 품질을 함께 측정하여 ADR에 최종 선택을 기록한다.

검증:

- 500개 factor node 문서 입력 성능
- 한글 IME
- undo/redo 및 view 전환 후 history
- YAML worker와 Vite build
- route lazy loading 및 bundle 크기
- JSON Schema completion/hover/diagnostic
- keyboard/accessibility
- JSON Pointer와 editor range 매핑

### P0-03 — Backend parser와 cross-runtime YAML 1.2 ADR

Frontend와 backend 모두 YAML 1.2 core schema로 같은 source를 동일하게 해석해야 한다. frontend `yaml` 계열과 backend YAML 1.2 parser 후보를 분리 검증하며, PyYAML 기본 YAML 1.1 implicit typing은 허용하지 않는다.

공유 fixture:

- `yes/no/on/off` 문자열
- `010`, `1_000`, `1`, `1.0`, `1e-2`
- ISO date 문자열
- enum case
- duplicate key와 multi-document
- alias, merge key, custom tag
- non-finite number와 negative zero

Acceptance:

- 양쪽 parser가 동일한 JSON-compatible typed tree를 만든다.
- typed StrategySpec hydration 이후 canonical bytes와 hash가 동일하다.
- date는 암묵적으로 datetime object가 되지 않는다.
- parser별 implicit coercion 차이가 있으면 fail-closed diagnostic을 반환한다.
- parser 선택과 안전 제한을 backend dependency ADR에 기록한다.

### P0-04 — Frontend router ADR와 deep-link spike

현재 dependency에 router가 없으므로 editor 선택과 분리하여 비교·결정한다.

검증:

- `/research/strategies/new`와 revision direct entry
- query parameter의 view/path/date 복원
- browser back/forward와 dirty navigation blocker
- app provider 및 TanStack Query integration
- route lazy loading과 not-found/error boundary
- 기존 single-page entry redirect

Acceptance:

- 선택한 router와 version을 dependency ADR에 기록한다.
- FSD app→pages→widgets 방향을 깨지 않는 route composition 예제가 있다.
- P2-02가 구현할 route tree와 rollback 방법이 확정된다.

Phase 0 종료 기준:

- authoring 계약, frontend editor, backend parser, frontend router 선택에 미결정 사항이 없다.
- 이후 PR의 dependency와 rollback 방법이 기록되어 있다.

---

## 5. Phase 1 — Backend Authoring Contract

### P1-01 — Canonical document hydrate

- `StrategySpec → identity-free canonical payload → StrategySpec` 역변환
- 지원하지 않는 schema version fail-closed
- draft/saved identity 외부 주입
- parse tree를 반드시 typed StrategySpec으로 hydrate한 뒤 canonicalize
- 기존 hash golden 유지

Acceptance:

- round-trip 후 identity 외 모든 필드가 동일하다.
- 기존 전략 hash가 변하지 않는다.
- 현재 배열 순서 semantics를 그대로 유지한다.
- `1`/`1.0`, `15`/`15.0`, `1e-2`, ISO date 문자열, enum case fixture를 통과한다.
- untyped dict를 직접 canonical JSON으로 직렬화하는 우회 경로가 없다.
- unknown key는 depth와 무관하게 structural fail-closed다 (P0-01 ADR D5).

### P1-02 — 안전한 YAML/JSON codec

- YAML 1.2 단일 document
- duplicate key, custom tag, merge key 차단
- anchor/alias 거부; bytes/depth/node count 제한 (compose 전 scan 단계에서 fail-closed)
- codec은 untyped tree + JSON Pointer source map만 만든다 (`DocumentCodecPort`); typed hydrate는 domain
- PyYAML `import yaml` 금지 architecture test (ruamel.yaml YAML 1.2 loader만 허용)
- non-finite number와 비문자열 key 차단
- JSON Pointer ↔ line/column/offset source map
- exact UTF-8 source 보존

Acceptance:

- 주석 포함 YAML을 typed StrategySpec으로 변환한다.
- 같은 의미의 YAML/JSON은 같은 spec hash를 가진다.
- 악성 alias/tag/duplicate fixture는 위치 diagnostic과 함께 실패한다.

### P1-03 — Compile API

```text
POST /api/v1/strategy-documents/compile
```

응답:

```text
source_hash
schema_version
spec | null
canonical_json | null
spec_hash | null
diagnostics[]
```

Diagnostic 계약:

```text
code
severity
kind: syntax | structural | semantic | capability
pointer
range
node_id?
message
```

Acceptance:

- syntax, structure, semantic 오류를 하나의 배열로 반환한다.
- `/risk/max_name_weight`가 정확한 YAML scalar 범위를 가리킨다.
- missing field는 가장 가까운 parent range를 가리킨다.
- invalid source에는 spec/hash를 반환하지 않는다.
- 기존 validation API는 호환을 유지한다.
- OpenAPI와 generated SDK가 같은 PR에 포함된다.

### P1-04 — Strategy constraint catalog owner

scalar field constraint와 UI contract metadata의 owner를 backend domain의 단일 선언으로 만든다.

소유 정보:

- type과 enum
- minimum/maximum
- unit과 display unit
- default와 example
- applied stage/timing
- description key

원칙:

- scalar validator와 schema extension이 같은 declaration을 소비한다.
- cross-field와 graph semantic constraint는 기존 domain validator가 소유하되 노출 가능한 contract code를 연결한다.
- domain은 Pydantic을 import하지 않고 stdlib dataclass/type metadata를 유지한다.
- (구현 결과, 최초 계획과 다름) inbound schema adapter의 transport alias는 만들지 않았다.
  `kind` → node type 매핑의 owner는 domain `EXPRESSION_NODE_KINDS`(`domain/factor/_nodes.py`)이고,
  runtime JSON Schema의 `discriminator`는 domain schema builder(`domain/strategy/_schema.py`)가
  union member의 `kind: Literal[...]`에서 직접 만든다. Pydantic `Annotated[..., Field(...)]`
  alias는 없다.
- 어느 쪽도 domain dataclass의 field를 복제하지 않으므로 hand-written DTO로 취급하지 않는다.
- `FactorNode` union은 `kind` literal union으로 노출되어 SDK가 narrowing할 수 있다. OpenAPI
  문서에는 `discriminator`가 0개이고 runtime schema 응답에만 있다.
- schema 응답에 range/unit을 별도 hand-written table로 복제하지 않는다.

Acceptance:

- `max_name_weight` range를 바꾸면 validation과 schema가 함께 바뀐다.
- 모든 scalar validation code가 constraint catalog 또는 명시적 semantic-only 목록에 속한다.
- FactorNode schema에 `kind` discriminator와 variant별 required field가 존재한다.
- architecture test가 domain/application의 Pydantic import를 금지한다.
- `EXPRESSION_NODE_KINDS`와 domain `ExpressionNode` union의 variant 집합이 같다는 parity test가
  있다 (`tests/domain/test_strategy_constraints.py::test_expression_node_kinds_cover_the_union_exactly`).

### P1-05 — Runtime Schema·Contract API

```text
GET /api/v1/strategy-documents/schema
GET /api/v1/strategy-documents/contract
```

제공 정보:

- JSON Schema
- 타입, enum, 범위, 단위, 기본값, 예시
- 적용 실행 단계
- Factor Registry와 Dataset Registry version/link
- schema hash와 ETag

Acceptance:

- 모든 authoring path가 schema에서 탐색된다.
- 모든 FactorNode `kind`가 discriminated union으로 노출된다.
- template이 runtime schema를 통과한다.
- 별도 hand-written schema와 DTO가 없다.

### P1-06 — Immutable revision source envelope

- repository가 StrategySpec 대신 revision record를 보관하도록 확장
- repository port에 list/history query와 pagination contract 추가
- exact source, format, source hash, provenance 저장
- legacy revision은 `source=None`으로 호환
- canonical JSON은 spec에서 파생
- optimistic revision conflict 유지

Acceptance:

- revision 1 source가 revision 2 생성 후에도 불변이다.
- `compile(stored source).spec_hash == record.spec_hash`이다.
- stale expected revision은 409이다.
- 기존 JSON create/get 흐름이 유지된다.
- memory와 persistent adapter가 동일한 repository contract test를 통과한다.

### P1-07 — Document save/get/history API

```text
POST /api/v1/strategy-documents
POST /api/v1/strategy-documents/{id}/revisions
GET  /api/v1/strategies/{id}/revisions
GET  /api/v1/strategies/{id}/revisions/{revision}/document
```

Acceptance:

- compile 성공한 exact source만 저장한다.
- source가 없는 legacy revision은 generated source와 provenance를 반환한다.
- history ordering과 pagination이 결정적이다.
- create/revise가 identity를 source 밖 envelope에 할당한다.

### P1-08 — Semantic diff API

- revision 간 canonical payload diff
- `pointer`, `added|removed|changed`, `before`, `after`
- source comment와 identity 변화 제외
- 현재 canonical order semantics 유지

### P1-09 — Backtest strategy reference와 run manifest

기존 inline `BacktestRunSpec.strategy`는 호환을 유지하면서 saved revision reference를 additive하게 지원한다.

```text
strategy_source:
  saved_revision:
    strategy_id
    revision
    expected_spec_hash
  OR inline_draft:
    spec
    source_hash?
```

- backend는 실행 전에 saved revision을 resolve하고 expected hash를 확인한다.
- run manifest/result에 resolved strategy ID, revision, spec hash, schema version을 남긴다.
- draft backtest는 inline spec을 허용하되 live deployment에는 사용할 수 없다.
- 미래 deployment는 saved revision reference만 허용한다.

Acceptance:

- stale 또는 hash mismatch revision은 실행 전에 실패한다.
- inline legacy 요청은 계속 동작한다.
- result에서 실행한 정확한 revision/hash를 확인할 수 있다.
- manifest serialization과 artifact hash가 결정적이다.

Phase 1 종료 기준:

- source compile → save → get → recompile 후 source hash와 spec hash가 보존된다.
- 모든 API PR의 generated SDK tree가 clean하다.
- scalar validation과 schema metadata가 같은 constraint declaration에서 파생된다.
- backtest result가 실행한 strategy revision 또는 inline provenance를 기록한다.

---

## 6. Phase 1.5 — Backtest Correctness Gate

이 Phase는 디버거 기능이 아니다. 현재 portfolio preview와 backtest가 FactorGraph 대신 factor ID 기반 synthetic 값을 사용할 수 있는 정확성 결함을 먼저 제거한다. P0-01 직후 P1-01과 병행 착수할 수 있고, P3-05 YAML Backtest cutover보다 반드시 먼저 끝나야 한다.

### P1.5-01 — Snapshot provenance

- client가 snapshot ID를 임의로 정하지 않음
- data adapter가 실제 snapshot ID 반환
- expected/actual snapshot mismatch fail-closed
- cache key에 실제 snapshot 사용

### P1.5-02 — Factor evaluation projection

- evaluator 내부 computed node cache를 immutable projection으로 승격
- 요청 node/security/date만 반환
- output projection이 기존 FactorEvaluation output과 동일
- warm-up, missing input, divide-by-zero, group missing 구분

이 primitive는 correctness parity 검증에 사용하며 public debug API는 Phase 5에 둔다.

### P1.5-03 — Raw PIT observation port

- snapshot ID와 sessions
- historical universe membership
- raw fields와 available date
- sector/group
- previous weight
- factor graph minimum history

Acceptance:

- 미래 공개 **필드** 데이터가 preview/backtest에 나타나지 않는다. `sector_id`·
  `universe_member`는 공개일이 없어 가드 범위 밖이며 as_of vintage는 어댑터 책임이다
  (D-006).
- mock과 향후 production adapter가 같은 port contract를 통과한다.

### P1.5-04 — Truthful preview/backtest pipeline

```text
raw PIT data
→ factor plan
→ node evaluation
→ factor contribution
→ composite score
→ eligibility
→ rank/selection
→ unconstrained target
→ constrained target
→ backtest
```

Acceptance:

- CandidateDecision의 factor 값이 FactorGraph output과 동일하다.
- composite score가 factor output×direction×weight 합과 동일하다.
- Portfolio preview와 Backtest가 같은 TargetTape pipeline을 사용한다.
- factor ID hash 기반 synthetic `_portfolio_factor_value` 경로를 제거한다.
- 기존 결과가 바뀌면 정확성 수정으로 명시하고 golden과 release note를 함께 갱신한다.

Phase 1.5 종료 기준:

- 현재 M5 backtest가 실제 FactorGraph 정의를 사용한다.
- PIT, factor output, TargetTape, backtest 입력 parity test가 통과한다.

---

## 7. Phase 2 — App Shell과 시각 기반

### P2-01 — Light semantic theme token과 primitive

- 시안과 같은 밝은 중성 theme 하나만 구현
- 본문 14px, 보조 정보 최소 12px
- surface, border, text, status, focus semantic token
- Button, Tabs, Badge, Tooltip, EmptyState, SplitHandle
- 기존 동작을 바꾸지 않고 CSS 구조 분리

Acceptance:

- 기존 화면 회귀가 없다.
- focus-visible과 색 대비를 만족한다.
- 상태를 색상 하나로만 구분하지 않는다.
- ko/en 문구가 함께 추가된다.

### P2-02 — Router와 App Shell

P0-04에서 선택한 router와 route composition을 구현한다.

초기 route:

```text
/research/strategies/new
/research/strategies/:id/revisions/:revision
/research/backtests/:runId
```

미래 namespace:

```text
/operations/deployments
/operations/orders
/operations/positions
/operations/risk
```

- 운영 route는 항상 등록하되 실제 기능 전까지 feature flag가 꺼져 있으면 `beforeLoad`에서 not-found로 처리한다.
- 기존 진입 URL: query 없는 `/`는 `/research/strategies/new`로, `/?step=`·`/?run`은 query를 유지한 채
  `/legacy/builder`로 redirect한다 (router ADR D2). P6-06 legacy 제거 후에는 `/legacy/builder`도
  `/research/strategies/new`로 redirect한다.
- dirty navigation blocker는 pathname이 바뀌는 이동만 차단하고 search-only 변경(view/path/date 선택)은
  차단하지 않는다 (router ADR D3).
- app은 router/provider만 소유하고 page는 FSD public API를 사용한다.

Acceptance:

- direct URL, reload, back/forward가 동작한다.
- loading, error, 404 상태가 구분된다.
- 미래 운영 메뉴가 실제 주문 기능처럼 오인되지 않는다.

### P2-03 — Strategy IDE layout

- 왼쪽 Strategy Outline
- 중앙 source editor
- 오른쪽 Contract Inspector
- 하단 Intermediate Debugger
- resize/collapse
- 1280px 미만 inspector/debugger drawer
- 좌측 outline에 `parameters` 포함
- 중복되는 상단 Data→Factor→Portfolio→Risk→Execution stepper 제거

이 PR은 placeholder 데이터만 사용하고 도메인 동작을 넣지 않는다.

### P2-04 — Revision-aware loader

관리 상태:

```text
source
sourceVersion
compiledSpec
compiledVersion
baseRevision
baseSpecHash
savedSource
dirty
diagnostics
```

Acceptance:

- new/template과 saved revision 진입을 구분한다.
- create 성공 후 revision URL로 전환한다.
- load, empty, error 상태를 구분한다.
- dirty route leave를 차단한다.

Phase 2 종료 기준:

- 새 App Shell에서 전략과 백테스트 route를 직접 열 수 있다.
- legacy editor는 아직 별도 route에서 사용할 수 있다.

---

## 8. Phase 3 — YAML Editor MVP

### P3-01 — Frontend document state machine

```text
editing
→ parsing
→ structurally-valid
→ semantically-valid
→ saved

editing
→ syntax-invalid | structure-invalid | semantic-invalid
```

- source CST 기반 path/range index
- P0-03에서 고정한 YAML 1.2 core schema와 cross-runtime fixture 사용
- parse 실패 시 compiled spec을 덮어쓰지 않음
- parse trigger는 editor의 `view.composing`이 false일 때만 실행 (한글 조합 중 보류)
- stale spec은 명시적 badge와 함께 조회만 가능
- source format은 YAML 또는 JSON 중 하나

### P3-02 — Code editor adapter

- lazy-loaded editor와 worker
- line number, folding, search, bracket matching
- indentation, undo/redo
- Korean IME
- keyboard navigation
- model/worker lifecycle dispose

도메인 중립 wrapper만 `shared/ui`, StrategySpec 연결은 `features/edit-strategy`에 둔다.

### P3-03 — Schema 탐색 completion·hover

- backend runtime schema 기반 completion/hover (`$ref`/`oneOf` + `kind` discriminator 해소)
- required/type/enum/unknown-key marker는 frontend가 계산하지 않고 P3-04 backend diagnostic이 붙인다 (editor ADR D2)
- 한글 조합 중(`view.composing`)에는 parse/backend 호출을 보류한다
- Factor `kind` 변경에 따른 허용 field 변경
- Dataset field와 Factor Registry completion
- frontend 수기 허용 목록 금지

### P3-04 — Semantic validation marker

- backend compile/validate debounce
- 이전 요청 취소
- sourceVersion이 다른 응답 폐기
- diagnostic pointer를 source range에 연결
- warning과 error 분리

Acceptance:

- 응답 역전 시 오래된 diagnostic이 나타나지 않는다.
- invalid source에서 preview 요청이 나가지 않는다.
- 문제 선택 시 정확한 source 위치로 이동한다.
- 한글 조합 중에는 backend validate를 호출하지 않는다.

### P3-05 — Toolbar·Save·Backtest cutover

표시:

- revision
- schema version
- source hash
- spec hash
- dirty 및 validation 상태
- Validate / Save Revision / Backtest

Acceptance:

- edit → validate → create/revise → reload가 동작한다.
- Backtest는 현재 검증된 StrategySpec만 사용한다.
- `dirty == false`, `baseRevision != null`, `compiled spec hash == baseSpecHash`이면 saved revision reference를 사용한다.
- 그 외 current document가 valid/current이면 inline draft와 source/spec provenance를 사용한다.
- 새 전략, 저장 후 추가 수정, base hash 불일치는 saved revision으로 실행하지 않는다.
- invalid/stale이면 saved reference fallback 없이 실행을 차단한다.
- backend canonical hash만 표시한다.
- invalid/stale document에서 Save·Run·Debug가 비활성화된다.
- Quick/Advanced를 전제한 ko/en 문구(`builder.subtitle` 등)를 YAML-first 표현으로 함께 갱신한다.
- Phase 1.5 correctness parity가 완료되지 않으면 YAML route의 Backtest 정식 cutover를 허용하지 않는다.

### P3-06 — Local autosave와 recovery

- draft ID 또는 `strategyId/baseRevision`별 저장
- source, base hash, schema version, timestamp
- reload 시 서버 원본과 복구본 비교
- schema version 불일치 시 raw source 다운로드
- 저장 성공 후 recovery 정리

### P3-07 — Revision conflict 안전 처리

- 409에서도 사용자 source를 보존한다.
- 서버 revision과 현재 base revision을 표시한다.
- `서버본 열기`, `현재 문서 복사`, `Diff 열기`를 제공한다.
- 자동 merge는 semantic diff 화면 완성 전에는 제공하지 않는다.

Phase 3 종료 기준:

- 새 전략 작성, revision 수정, 오류 수정, 저장, recovery, backtest 시작이 YAML 화면에서 동작한다.
- 이 시점부터 YAML route를 beta 기본값으로 사용할 수 있다.

---

## 9. Phase 4 — Outline·Contract·Projection

### P4-01 — Strategy Outline

- basic information, data, eligibility, factors, signal, portfolio, risk, execution, parameters tree
- tree 선택 → source 이동
- cursor path → tree 선택
- parse error 중 가능한 subtree 유지
- array index와 node ID를 안정적으로 구분
- ARIA tree와 keyboard navigation

### P4-02 — Contract Inspector

선택 path에 대해 다음을 표시한다.

- type, enum, default, range
- unit, description, example
- applied stage/timing
- registry provenance와 PIT metadata
- union/discriminator 정보

### P4-03 — Problems panel

- syntax/structural/semantic/capability filter
- error와 warning 분리
- 문제 선택 시 editor jump
- pointer/node ID 복사
- 동일 diagnostic 중복 제거

### P4-04 — Execution Plan query orchestration

- current valid StrategySpec만 backend factor explain API에 전달
- compiled/runtime schema와 request/response dataset·factor-registry version coherence를 fail-closed로 확인
- 모든 factor query의 identity와 cancellation을 일관되게 관리
- field/group metadata는 backend data adapter가 resolve하고 browser metadata 입력은 신뢰하지 않음
- plan 유무와 무관하게 backend registry/dataset provenance를 응답
- plan, type, unit, history, fingerprint는 backend 응답을 SoT로 유지
- factor/node와 exact JSON Pointer 사이의 source mapping 제공

### P4-09 — Execution Plan projection

- backend factor explain/plan 표시
- topological order
- input/output type과 unit
- minimum history
- cache/plan fingerprint
- source 또는 graph node와 selection 연동

### P4-05 — Canonical snippet model and editor transaction

- backend runtime schema와 coherent factor catalog에서 data/factor/signal/risk/execution snippet 값을 파생
- frontend 수기 StrategySpec shape·enum·default·factor graph 금지
- cursor 위치와 YAML indentation을 인식한 range edit plan
- 전체 next source의 YAML 1.2 parse preflight
- 문자열 append 대신 editor-agnostic 단일 undoable transaction으로 삽입

### P4-10 — Snippet catalog UI and page wiring

- five-area snippet catalog를 left IDE panel에 표시
- new/revision source editor와 같은 transaction coordinator를 합성
- loading/unavailable/success/failure와 IME 차단 피드백
- keyboard/accessibility와 실제 route integration 검증

### P4-06 — JSON·Form projection

- JSON: current valid spec의 read-only canonical projection
- Form: metadata/data/portfolio/risk/execution의 read-only 요약
- invalid source에서는 last valid value를 `stale`로 표시
- view 전환 후 source와 undo history 보존
- Form 전체 reserialize로 YAML comment를 삭제하지 않음

### P4-07 — Graph projection

- existing factor node list를 read-only DAG로 교체
- graph node ↔ YAML path 양방향 선택
- branch, conditional, subgraph 표현
- cycle, missing input, type/unit issue 표시
- graph 직접 편집은 후속 범위

### P4-08 — Diff view와 conflict resolution

- source text diff
- semantic canonical diff
- revision-to-revision diff
- invalid source에서는 text diff만 제공
- comment-only 변경은 semantic diff에서 제외
- 409 conflict에서 새 revision 생성 또는 변경 복사

Phase 4 종료 기준:

- 시안의 좌측, 중앙, 우측 영역과 YAML/JSON/Form/Graph/Diff view가 완성된다.

---

## 10. Phase 5 — 신뢰 가능한 중간 디버거

Phase 1.5에서 실제 preview/backtest 계산 경로를 먼저 바로잡은 뒤, 그 경로를 조회하는 bounded trace API와 UI만 이 Phase에서 추가한다.

### P5-01 — Scoped trace API

```text
POST /api/v1/strategies/debug/trace
```

요청:

- draft spec 또는 saved revision
- as-of date와 security IDs
- factor/node filter
- raw 포함 여부
- optional starting holdings

응답 provenance:

```text
spec_hash
snapshot_id
registry_version
plan_hash
```

Acceptance:

- 같은 요청 결과 ordering이 결정적이다.
- page/row limit와 cancellation이 존재한다.
- invalid/capability failure는 계산 전에 diagnostic으로 실패한다.

### P5-02 — Debugger shell

- collapse/resize panel
- date, security, factor, node selection
- 기존 TargetTape의 score/rank/selected/exclusion/target 우선 표시
- response fingerprint/sourceVersion mismatch 폐기
- invalid spec에서는 요청하지 않음

### P5-03 — Full linked trace UI

```text
Raw
→ Lag
→ Winsorize
→ Neutralize
→ Rank
→ Contribution
→ Composite Score
→ Selection
→ Unconstrained Target
→ Risk-constrained Target
→ Estimated Order Delta
```

상태 구분:

- 실제 0
- missing
- source omitted
- coverage gap
- warm-up 부족
- 계산 제외
- risk constraint 제거

Order Estimate는 starting holdings가 있는 경우에만 표시하고 실제 주문으로 오인되지 않게 가정과 시점을 함께 표시한다.

Phase 5 종료 기준:

- UI trace 한 행이 실제 FactorGraph, TargetTape, backtest 입력과 일치한다.
- 동일 spec/snapshot/plan fingerprint로 결과를 재현할 수 있다.

---

## 11. Phase 6 — 전문 사용자 마감과 전환

### P6-01 — Persistent strategy repository

- memory repository를 SQLite adapter로 교체
- revision source/hash 저장과 migration
- deterministic pagination
- 기존 repository port 유지
- process restart 후 strategy 복원

### P6-02 — Server draft persistence/CAS/recovery UI

- server draft optimistic lock
- multi-device draft conflict
- local recovery는 서버 장애 fallback으로 유지

### P6-03 — Keyboard·Command Palette

- Validate, Save, Backtest
- view/panel 전환
- path와 symbol 검색
- theme와 panel size 저장
- 주요 흐름을 mouse 없이 완료

### P6-04 — 성능·접근성 hardening

- 500-node spec performance budget
- editor lazy chunk
- trace virtualization
- focus trap과 accessible name
- 1440/1920 light/dark 확인
- soft dark theme token을 P6-03의 저장된 theme preference에 연결
- ko/en 문구 완결

### P6-05 — Browser E2E·visual regression 기반

- P6-04의 soft dark theme와 접근성 hardening이 완료된 뒤 baseline을 생성
- Playwright와 CI script
- direct route/editor worker 테스트
- 1440/1920 light/dark screenshot baseline
- dependency와 test infrastructure만 추가

### P6-06 — E2E 시나리오와 legacy 제거

필수 시나리오:

- new → edit → validate → save → reload
- invalid source → marker → fix
- autosave recovery
- revision conflict
- factor trace와 exclusion/risk constraint
- backtest start/result 연결
- revision diff

위 시나리오가 통과한 뒤 P0-01에서 승인한 YAML-first 전환과 migration acceptance를 확인한다. Quick/Advanced editor 삭제는 roadmap·규칙 갱신과 사용자 migration 조건이 모두 충족된 경우에만 별도 cleanup commit으로 수행한다. 조건이 충족되지 않으면 legacy route를 유지하고 제거 작업은 별도 initiative로 넘긴다.

### P6-07 — Root development entrypoints

저장소 루트의 개발 명령은 실행 위치만 위임한다. frontend Vite 설정은
`frontend/package.json`, backend ASGI target·host·port·reload 설정은 backend bootstrap이
각각 단일 owner다. 루트 wrapper가 이 설정이나 runtime dependency를 복제하지 않는다.

Acceptance:

- 저장소 루트의 `npm run dev`가 `frontend`의 Vite 개발 서버를 실행한다.
- 저장소 루트와 `backend` 디렉터리 모두에서 `uv run server`가 같은 FastAPI/Uvicorn
  development server를 실행한다.
- 각 명령은 표준 입출력과 종료 신호를 하위 프로세스에 전달하고 non-zero 종료를 숨기지 않는다.
- README quick start와 실제 HTTP smoke test가 명령 계약을 고정한다.
- production process manager, Docker, 양쪽 서버 동시 실행은 이 PR의 범위가 아니다.

### P6-08 — Strategy/revision history routed UI

- deterministic strategy list와 revision history pagination
- 원하는 immutable revision 편집·diff direct route 연결
- loading, empty, error, out-of-range URL 상태

### P6-09 — Backtest run history routed UI와 provenance

- process-lifetime run lifecycle의 newest-first pagination과 strategy filter
- saved/inline provenance의 strategy/revision/spec/schema/source hash 표시
- run detail direct route 연결과 nonterminal polling

Phase 6 종료 기준:

- latest main에서 backend/frontend CI와 browser E2E가 모두 통과한다.
- legacy route 없이 전체 핵심 사용자 흐름이 가능하다.

---

## 12. PR 크기와 범위 규칙

- 사용자에게 설명 가능한 동작 또는 invariant 한 개
- handwritten diff 150–450 lines 권장
- production+test logical files 약 8개 이내
- backend endpoint는 하나의 use case
- test는 구현과 같은 PR에 포함
- generated SDK/OpenAPI/lockfile은 줄 수에서 제외
- behavior change와 광범위 cleanup 분리
- 600 lines 또는 logical files 10개 초과 시 기본적으로 분할
- 불가피하게 초과하면 PR 본문 `제약사항`에 분할 불가 이유 기록

API 변경은 반드시 같은 PR에서 OpenAPI와 generated SDK를 갱신한다.

## 13. PR 실행 워크플로우

```text
Scope packet
→ implementation + tests
→ author self-check
→ diff freeze
→ fresh review sub-agent 1명
→ changes if required
→ same reviewer re-review
→ APPROVE
→ latest main + CI
→ merge
```

### 13.1 Scope packet

구현 전 PR 본문 초안을 작성한다.

- Phase/PR ID
- intent
- acceptance criteria
- intentional non-goals
- 변경되는 contract/SoT
- 유지할 기존 동작
- 예상 파일
- test plan
- rollback 방법

### 13.2 Author self-check

| PR 유형 | 리뷰 요청 전 gate |
|---|---|
| Frontend UI | focused Vitest → typecheck → lint → full test → build |
| Backend | focused pytest → full pytest → Ruff → Pyright |
| API contract | backend full → API generate → generated diff 확인 → frontend full |
| Rust/engine | backend full → cargo fmt/clippy/test → parity |
| Dependency/CI | clean install → full suite → lockfile review |
| Documentation | link, path, command verification |

테스트는 CSS class나 내부 state가 아니라 사용자 결과와 wire contract를 검증한다.

### 13.3 Diff freeze

리뷰 시작 직전에 다음을 `PLAN.md`에 기록한다.

- merge-base SHA
- review HEAD SHA
- diff stat
- 실행 명령과 결과
- known baseline failure
- manual UX 결과

리뷰어가 활동 중일 때 구현자는 해당 diff를 수정하지 않는다.

### 13.4 Review sub-agent

PR마다 fresh review-only sub-agent 정확히 한 명을 사용한다.

- 이름: `review_p3_04` 형식
- fresh context
- code edit 금지
- PR body, rules, base/head diff, caller, test를 읽음
- 첫 리뷰 이후 수정 재검토는 같은 agent에 전달
- 새 agent를 추가하지 않아 PR당 reviewer 한 명을 유지

리뷰 프롬프트:

```text
역할: 이 PR의 독립적인 senior reviewer다.
코드를 수정하지 말고 read-only로 검토하라.

Phase / PR:
Intent:
Acceptance criteria:
Intentional non-goals:

Base SHA:
Head SHA:

필수 invariant:
- StrategySpec이 실행 의미의 SoT
- invalid/stale source 실행 금지
- backend canonical hash 사용
- generated SDK 외 hand-written DTO 금지
- PIT/look-ahead 금지
- trace와 실제 execution path 일치

검토:
- base...head 전체 diff와 caller
- wire contract와 generated artifacts
- source/spec drift
- race, debounce, cancellation, revision conflict
- FSD와 backend package boundary
- keyboard, accessibility, ko/en
- performance와 response size
- 실제 사용자 결과를 검증하는 test

Finding:
### [P0|P1|P2|P3] 제목
- 상황:
- 인풋:
- 위치:
- 위험성:
- 권장 수정:
- 필요한 회귀 테스트:

VERDICT: APPROVE | REQUEST_CHANGES | BLOCKED_NEEDS_DECISION
BLOCKING_FINDINGS:
RESIDUAL_RISKS:
TESTS_RUN:
```

### 13.5 판정과 재검토

- P0: data loss, security, look-ahead, wrong order/weight, reproducibility 파괴
- P1: acceptance 실패, major flow 회귀, contract/SoT 위반, stale source 실행
- P2: 실질적인 edge case, performance, maintainability 문제
- P3: naming과 작은 후속 개선

판정:

- P0/P1이 하나라도 있으면 `REQUEST_CHANGES`
- P2는 수정하거나 후속 issue와 명확한 근거를 남김
- P3만으로 merge를 막지 않음
- 수정 후 변경 HEAD와 finding별 처리 결과를 같은 reviewer에게 전달
- reviewer는 finding뿐 아니라 갱신된 전체 diff의 신규 회귀도 확인

### 13.6 Merge gate

- scope packet과 실제 diff 일치
- reviewer 최종 `APPROVE`
- blocking finding 0
- latest main 반영 후 전체 CI green
- OpenAPI generation 후 예상하지 않은 diff 없음
- generated SDK 외 hand-written DTO 없음
- source/spec/hash invariant 통과
- keyboard/focus/accessible name 확인
- ko/en 메시지 동시 반영
- docs/roadmap 갱신 여부 기록
- 관련 없는 working tree 변경 없음

### 13.7 Tracker 자동 갱신

PR row의 checkbox와 상태가 상세 진행의 입력값이다. frontmatter, 전체 progress와 Phase 집계는 손으로 맞추지 않고 다음 스크립트로 계산한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File docs/planning/strategy-workbench-yaml-ui/tools/update-plan-progress.ps1
```

검증만 할 때:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File docs/planning/strategy-workbench-yaml-ui/tools/update-plan-progress.ps1 -Check
```

- `[x]`는 반드시 `MERGED`, `MERGED`는 반드시 `[x]`여야 한다.
- active status는 `IN_PROGRESS`, `SELF_CHECK`, `IN_REVIEW`, `CHANGES_REQUESTED`, `APPROVED`다.
- 기본 active PR은 하나다.
- Phase 1과 Phase 1.5의 병렬 window처럼 `PLAN.md`에 명시되고 별도 worktree를 쓰는 경우 최대 두 개까지 허용한다.
- 같은 worktree 위에 순서대로 쌓인 stack(예: P2-01→P2-02→P2-03→P3-01→P3-02)은 `parallel_window`에 전부 나열했을 때 한 line으로 보고 두 개 제한을 넘겨 리뷰를 병행할 수 있다. merge는 stack 아래부터 순서대로 하며, 아래 PR이 CHANGES_REQUESTED가 되면 위 PR로 전진 병합한다.
- reviewer는 tracker를 수정하지 않고 구현 책임자가 verdict와 CI 결과를 반영한다.

## 14. Phase 종료 Gate

각 Phase는 다음 조건을 모두 만족해야 종료한다.

1. 하위 PR이 main에 merge되었다.
2. Phase acceptance scenario가 frontend → generated SDK → backend까지 통과한다.
3. source/canonical round-trip과 hash가 보존된다.
4. frontend가 validation formula 또는 DTO를 복제하지 않는다.
5. keyboard, focus, accessible name을 확인한다.
6. ko/en 메시지가 함께 갱신된다.
7. 관련 README, roadmap, architecture 문서가 갱신된다.
8. 죽은 feature flag와 issue 없는 TODO가 없다.
9. latest main에서 전체 CI가 성공한다.
10. 모든 PR에 reviewer `APPROVE` 기록이 있다.

## 15. 자동매매 확장 경계

이번 계획은 실제 live trading을 활성화하지 않지만 다음 경계를 지금부터 유지한다.

- 연구 UI는 `/research`, 운영 UI는 `/operations`
- 배포는 mutable draft가 아닌 `strategy_id + revision + spec_hash`를 참조
- deployment manifest에 schema/data snapshot/registry/engine version/parameters 포함
- credentials는 source가 아닌 secret reference 사용
- execution mode는 backtest/shadow/paper/live로 분리
- `decision_id → order_id → fill_id → position` audit chain 보존
- Backtest action을 나중에 Live action으로 재사용하지 않음
- deployment, risk check, arming, kill switch는 별도 use case와 권한으로 구현

## 16. 완료 지점

- Phase 1.5: 현재 M5 backtest의 FactorGraph 정확성 복구
- Phase 3: YAML/JSON authoring MVP
- Phase 4: UI 시안의 IDE 영역 완성
- Phase 5: 복구된 실제 계산 경로를 조회하는 중간 디버거
- Phase 6: 전문 사용자 대상 정식 전환
