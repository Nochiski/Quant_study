# 전략 언어 2.0 · 파이프라인 캔버스 구현 워크플로우

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` 또는
> `superpowers:executing-plans`로 PR 단위로 실행한다. PR마다 [YAML Strategy Workbench WORKFLOW
> 13절](../strategy-workbench-yaml-ui/WORKFLOW.md) 절차(scope packet → self-check → diff freeze →
> Opus reviewer 1명 → 재검토 → APPROVE → merge)를 지킨다. 진행 상태는 [PLAN.md](./PLAN.md)만
> 갱신한다. Step 단위 절차는 PR 착수 시 `현재 작업 Packet`에 적는다.

**Goal:** 전략 언어에 전략 논리만 남기고(schema 2.0), 그 언어가 곧 화면이 되게 해서 비전공자가
파이프라인·팩터 탭만으로 전략을 만들어 백테스트까지 도달하게 한다. 측정은 퀀트 아이디어 5개다.

**Architecture:** backend가 2.0 모델·`steps` 컴파일러·업그레이더·연산자 카탈로그·`RunEnvironment`를
한 벌 소유한다. frontend는 runtime schema와 parse tree로 파이프라인·레시피·고급 캔버스를 그리고,
모든 GUI 동작을 기존 source 트랜잭션(`planSourceOperation` → `replaceRange` 한 번)으로 번역한다.
spec·hash·검증은 계속 backend compile만 믿는다. 계약은
[설계 spec](../../superpowers/specs/2026-09-20-strategy-language-2-0-and-pipeline-canvas-design.md)이
소유한다.

**Tech Stack:** Python 3.11 dataclasses · ruamel.yaml(safe + round-trip) · FastAPI · SQLite · DuckDB ·
React 19 · TanStack Router/Query · CodeMirror 6 · `yaml` 2.9 · Vitest · fast-check · MSW · Playwright ·
(P6) 그래프 라이브러리는 P6-01 ADR이 정한다

---

## 1. 실행 순서와 스택

```text
main
 └─ P0-01  docs/strategy-language-2-0-plan          (이 패키지 + spec + ADR·로드맵·SoT 개정)
     ├─ P1-01 ─ P1-02 ─ P1-03 ─ P1-04 ─ P1-05          화면 안 마찰 제거 (1.1 위, 독립)
     └─ P2-01 ─ P2-02 ─ P2-03 ─ P2-04 ─ P2-05 ─ P2-06 ─ P2-07   backend schema 2.0
         └─ P3-01 ─ P3-02 ─ P3-03                    frontend 2.0 적응
             └─ P4-01 ─ P4-02 ─ P4-03 ─ P4-04        파이프라인 캔버스
                 └─ P5-01 ─ P5-02 ─ P5-03 ─ P5-04    레시피 빌더
                     └─ P6-01 ─ P6-02 ─ P6-03        고급 노드 캔버스
```

- 브랜치 이름은 `feat/lang2-<pr-id 소문자>-<slug>`. 예: `feat/lang2-p2-02-strategy-spec-2-0`.
- 각 PR의 base는 직전 PR 브랜치다. P1 스택과 P2 스택은 서로 독립이라 병렬 진행할 수 있다
  (`parallel_window`에 기록). P3-01의 base는 P2-07이며 P1-05가 먼저 merge되어 있어야 한다(P3-01이
  P1의 i18n 키·진단 패널 위치를 전제한다).
- **generated SDK 규칙(1.1 initiative와 같음)**: P2 backend PR은 `backend/openapi.json`만 재생성하고
  `frontend/src/shared/api/generated`는 건드리지 않는다. SDK 재생성과 frontend 적응은 P3-01이 한
  PR에서 한다. P2 PR은 backend gate만 merge gate로 삼고 CI `frontend`·`browser-e2e` job은 P3-01·P3-03의
  exit 조건이다. 각 P2 PR 본문 `제약사항`에 이 사실을 적는다.
- 실 DB 주의: P2-07 merge 전에는 실 SQLite에 2.0 revision을 저장하지 않는다(spec 7절).
- 2.0 문서 fixture는 P2-02부터 `quality_momentum.yaml`이 2.0이 되고 1.1 원본은
  `quality_momentum.v1_1.yaml`로 보존한다(P2-07 golden).

## 2. 공통 gate

| PR 유형 | self-check |
|---|---|
| backend | focused pytest → `uv run pytest` 전체 → `uv run ruff check src tests` → `uv run pyright` → 계약(모델·facade 이름)이 바뀌면 루트에서 `uv run --project backend pytest database/tests -q` |
| API contract | backend 전체 → `uv run python scripts/export_openapi.py openapi.json` → diff 확인 (P3-01부터 `npm run api:generate` 포함) → frontend 전체 |
| frontend | focused vitest → `npm run typecheck` → `npm run lint` → `npm test` → `npm run build` |
| E2E 포함 | 위 + `npm run test:e2e` |

runtime schema fixture는 backend 모델이 바뀔 때마다 `uv run python tools/export_runtime_schema.py`로
재생성한다.

---

## 3. Phase 0 — 기획 패키지와 계약 문서

### P0-01 — 기획 패키지·spec·ADR/로드맵/SoT 개정

**Intent**: 이 패키지와 설계 spec을 main에 올리고, 상위 계약 문서가 이 initiative를 가리키게 한다.

**Acceptance**

- `docs/planning/strategy-language-2-0/{README,WORKFLOW,PLAN}.md`, `tools/update-plan-progress.ps1`
  (`-Check` 통과).
- `docs/superpowers/specs/2026-09-20-strategy-language-2-0-and-pipeline-canvas-design.md`.
- ADR 2026-09-04 머리말에 개정 링크(D2 문법, D8 대상 사용자). 1.1 spec 머리말에 개정 링크.
- 로드맵 완료 정의 문장을 원문("코드를 몰라도 …")으로 되돌리고 이 initiative 링크. M8 문구 조정.
- `.claude/rules/strategy-workbench-sot.md`에 "실행 설정", "연산자 정의", "steps 컴파일 규칙" owner
  행 예약(구현 PR이 채운다). 금지 절의 DSL 문장은 유지.

**Non-goal**: 코드 변경.

---

## 4. Phase 1 — 화면 안에서 끝나는 마찰 제거

1.1 계약을 바꾸지 않는다. 전부 2.0 뒤에도 그대로 남는 변경이다. P2와 병렬 가능.

### P1-01 — 문제 목록·검증 배지를 탭과 무관하게

**Intent**: Graph·Form 탭에서 편집 결과를 보려고 YAML 탭으로 돌아가는 왕복을 없앤다.

**Acceptance**

- `DiagnosticsPanel`과 문서 상태 배지("검증 통과"·"구조 오류"·STALE)가 `SourceEditor` 밖(IDE 편집
  영역 하단·툴바)으로 올라가 다섯 탭 모두에서 보인다.
- 문제 행 클릭 시 현재 탭이 YAML이면 줄로, Form·Graph이면 해당 pointer의 카드·필드로 이동(기존
  `onSelectPointer`). pointer가 현재 탭에 없으면 YAML 탭으로 전환.
- e2e `workbench.workflow.spec.ts` 그래프 시나리오에서 YAML 탭 전환 없이 "검증 통과"를 단언한다.

**예상 파일**: `widgets/strategy-ide/ui/strategy-ide.tsx`, `features/edit-strategy/ui/source-editor.tsx`,
`diagnostics-panel.tsx`, `document-toolbar.tsx`, e2e 1.

**Non-goal**: 진단 메시지 내용 변경(P1-05).

### P1-02 — 되돌리기·다시 실행 버튼과 전역 단축키

**Intent**: 탐색적으로 만져 볼 수 있게 한다.

**Acceptance**

- 툴바에 되돌리기·다시 실행 버튼. CodeMirror undo/redo 깊이를 읽어 비활성 판정.
- Ctrl+Z / Ctrl+Shift+Z(⌘)가 전역 단축키 처리기(`strategy-ide.tsx:280-316`)에 추가되어 편집기가
  hidden인 탭에서도 동작. 입력 필드에 포커스가 있으면 브라우저 기본 동작을 우선.
- Form·Graph 트랜잭션 한 번 = undo 한 단계(기존 불변식) 회귀 테스트.

**예상 파일**: `shared/ui/code-editor/*`(handle에 undo/redo/depth), `document-toolbar.tsx`,
`strategy-ide.tsx`, `shared/config/messages.ts`.

### P1-03 — 연산자 카탈로그(backend)와 노드·필드 한글 이름·설명

**Intent**: 화면 어휘를 사람 말로 바꾼다. 문장은 i18n, 키와 목록은 backend가 소유한다(spec D9).

**Acceptance**

- `domain/factor/_operators.py`에 `OperatorDefinition` 레지스트리(연산자 18개: kind, arity, params,
  output_type_rule, unit_rule, availability, description_key, formula_key, example). 스키마 enum과
  레지스트리 키가 같다는 테스트.
- `GET /api/v1/strategy-documents/operators`. runtime schema 노드 `$defs`의 `title`을 파이썬
  클래스명 대신 `x-description-key`로 대체하고, 모든 노드 property에 `x-description-key`.
- `messages.ts`에 노드 kind 12, 연산자 18, 노드 property 전부, 최상위 섹션 8개의 한글 이름과 한 줄
  설명. Form·Graph 라벨이 키 대신 이름을 보이고 `<code>` 키는 보조 표기로 내려간다.
- Contract Inspector가 i18n 키 문자열을 본문으로 찍는 경로 제거(누락 시 키 대신 "설명 없음").

**예상 파일**: backend `domain/factor/_operators.py`(신규), `_schema.py`, `adapters/inbound/http_api/_app.py`,
`openapi.json`; frontend `messages.ts`, `strategy-form-panel.tsx`, `factor-graph-editor.tsx`,
`contract-inspector.tsx`, `schema-navigator.ts`.

**Non-goal**: 파이프라인·레시피 화면(P4·P5). 연산자 `availability` 판정(P2-05).

### P1-04 — 연산자 먼저 고르기, 조용한 실패 피드백, 오류 본문 인라인

**Intent**: kind 분류 체계를 몰라도 노드를 추가하고, 실패가 보이게 한다.

**Acceptance**

- Graph 편집기의 "노드 추가"가 kind 드롭다운 대신 연산자 목록(P1-03 카탈로그, 그룹: 데이터·시간축·
  종목 간·계산·조건)을 보이고 kind는 `addNode`가 채운다. `nodeKinds` 파생 로직은 유지하되 UI에서
  숨긴다.
- `addNode` 실패(`unknown-kind`·materialize 실패)와 `addItemOperation === null`이 `TransactionFeedbackNote`
  또는 버튼 옆 문구로 이유를 보인다. 비활성 버튼에는 `aria-describedby`로 이유.
- 필드 오류 배지의 `title` 대신 필드 아래 `role="alert"` 본문. DAG 카드 fingerprint도 같은 방식.
- 삭제 거부 안내의 JSON Pointer 목록을 노드 표시 이름 목록으로.

**예상 파일**: `factor-graph-editor.tsx`, `graph-transactions.ts`, `strategy-form-panel.tsx`,
`form-transactions.ts`, `factor-graph-panel.tsx`, `messages.ts`, 테스트.

### P1-05 — 구조 오류 한글화, 진단 코드 네임스페이스, 순환·중복 진단에 node_id

**Intent**: 초보자가 가장 자주 만나는 오류 6종(unknown key, missing kind, unknown kind, bad enum,
missing window, type mismatch)이 전부 영문이다. SoT 규칙("compile 진단 message는 backend가 한글
문장으로")에 맞춘다.

**Acceptance**

- `structure.*`(`_hydrate.py`), `document.*`·`yaml.*`·`json.syntax`(`_codec.py`) 메시지 한글. `got=`·
  `expected=`·`allowed=` 진단 디테일은 유지(`error-messages.md`).
- `expected a sequence, got dict` 같은 1.0 문법 오류에 "업그레이드하세요" 힌트 코드
  (`structure.legacy_shape`)를 붙이고 frontend 업그레이드 배너가 이 코드에도 반응.
- `factor.*` 코드 전부를 `strategy.expression.*`로 alias(`code_aliases` 확장)하고 `EXPRESSION_CODES`
  레지스트리 게이트가 `factor.` 접두사를 통과시키지 않는다.
- `factor.graph.cycle`이 순환에 포함된 node_id 목록을, `duplicate_node`가 중복 id를 진단 `node_id`·
  메시지에 담는다. Graph 카드가 이를 하이라이트.
- 메시지 golden 테스트(코드별 1개).

**예상 파일**: backend `_hydrate.py`, `_codec.py`, `strategy/_validation.py`, `_constraints.py`,
`factor/_validation.py`, 테스트; frontend `upgrade-banner.tsx`, `factor-graph-panel.tsx`.

**Phase 1 exit**

- [ ] e2e 그래프 시나리오가 YAML 탭 전환 없이 통과.
- [ ] 노드 property·kind·연산자 설명 커버리지 100%(테스트로 고정).
- [ ] SoT·책임분리 점검 서브에이전트 blocking 0.

---

## 5. Phase 2 — backend schema 2.0

### P2-01 — `RunEnvironment` 모델과 실행 요청 확장(브리지)

**Intent**: 실행 설정을 전략 문서 밖에서 받을 자리를 먼저 만든다. 1.1과 완전 호환.

**Acceptance**

- `domain/backtest/_models.py`에 `RunEnvironment`(spec D7)와 canonical JSON·`environment_hash`.
- `BacktestRunSpec`, portfolio preview 요청, trace 요청이 optional `environment`를 받는다. 없으면
  `spec.data`·`spec.execution`·`graph.missing_policy`에서 만든다(브리지 함수 하나,
  `application/backtest_run/_environment.py`). 있으면 spec 값보다 우선.
- run manifest에 `environment`와 `environment_hash` 기록. 캐시 키에 포함.
- `backtest_run/_service.py`·`portfolio_design/_service.py`·`_trace_service.py`의 `spec.data.*`·
  `spec.execution.*` 직접 참조가 전부 `environment`를 거친다(grep 0건 테스트).
- OpenAPI 재생성. frontend SDK는 건드리지 않는다.

**Non-goal**: StrategySpec 변경.

### P2-02 — StrategySpec 2.0 (1): 실행 설정 제거, 버전 2.0, fixture·hash golden

**Intent**: spec D2의 "언어 밖으로" 절반. `data`·`execution` 섹션과 `graph.missing_policy`를 모델에서
지운다.

**Acceptance**

- `CURRENT_SCHEMA_VERSION = "2.0"`. `StrategySpec`에서 `data`·`execution` 제거. `FactorSignal.label`
  제거(`factor_id` → `id`로 rename), `FactorGraph.missing_policy` → `FactorSignal.missing`(기본 drop).
- `environment`가 실행 요청의 필수 필드가 된다(P2-01 브리지 제거). 없으면 422
  `backtest_run.environment_required`.
- hydrate·schema·validation·explanation·diff·trace·compile 경로 갱신. 1.1 문서는
  `structure.unsupported_schema_version`.
- fixture: `quality_momentum.yaml`(2.0, nodes 표기 유지), `.json`, `.legacy.json`, `.minimal.yaml`이
  같은 hash. 1.1 원본은 `quality_momentum.v1_1.yaml`로 보존. hash 알고리즘 golden 불변.
- runtime schema fixture 재생성. OpenAPI 재생성.

**제약**: P2-07 전까지 1.1 row는 읽을 수 없다(테스트 DB만). frontend는 P3-01까지 빨간불.

### P2-03 — StrategySpec 2.0 (2): `filter`·`combine`·`select`·`weight` 재구성

**Intent**: spec D4·D6의 섹션 모양. 모드 의존 필드 6개가 union으로 사라진다.

**Acceptance**

- `eligibility` → `filter`(rule: `field` | `steps`(P2-04 전까지 `field`만), `min`·`max`·`top_percent`·
  `top_count`; `liquidity_*` 흡수). `signal` → `combine`(`method` 3종, `score_threshold`, `regime`).
  `portfolio` → `select`(method union, `rebalance` 값 또는 `{every_sessions}`), `weighting`+`risk` →
  `weight`(method union, `by`).
- runtime schema가 `select`·`weight`를 `oneOf` + `x-discriminator: method`로 내보낸다.
  `FIELD_APPLICABILITY`는 union이 흡수하지 못한 행만 남긴다(`regime.min` ← `regime.field`,
  `short_count` ← `side`).
- 컴파일러(`domain/portfolio/_compiler.py`)가 새 섹션을 읽고 `combine.method`에 따라 정규화한다
  (`raw_weighted`가 1.1과 수치 동일한 회귀 테스트, `rank_weighted`·`zscore_weighted` 수치 테스트).
- `weight.method: inverse` + `by: <field_id>`가 1.1 `risk`와 같은 결과. `by: <factor id>`는 P2-06.
- 설명(`_explanation.py`)·semantic diff 경로 갱신. fixture 2.0 형태로.

### P2-04 — `steps` 표기 → `FactorGraph` 컴파일

**Intent**: spec D3. 배관 없는 팩터 표기.

**Acceptance**

- hydrate가 `factors[].steps`(단일 입력 체인, `inputs` 배열, `prev`, 중첩 `steps`, `when`, 소스 3종)를
  `FactorGraph`로 컴파일한다. node_id 생성 규칙 `s<n>`·`s<n>.<m>` 결정적. `output_node_id`는 마지막.
- `steps`와 `nodes`를 동시에 가지면 `structure.factor_dual_graph`. 첫 단계가 소스가 아니면
  `structure.steps_first_source`. 알 수 없는 연산자는 `structure.unknown_operator`(허용 목록은
  P1-03 카탈로그에서).
- `filter[].steps`도 같은 컴파일러를 쓴다(값은 필터 전용, 점수에 미포함).
- canonical payload는 `nodes`다. fixture `quality_momentum.yaml`을 steps로 바꾸고
  `quality_momentum.nodes.yaml`(같은 그래프의 nodes 표기)과 같은 hash golden.
- `saved_factor`·`saved_subgraph`를 노드 union에서 제거(실행 경로 없음, spec D3).
- 스키마: `steps` 항목은 `oneOf`(문자열 enum | 연산자별 단일 키 object). 완성·Form projection이 읽을
  수 있게 `x-operator` 마커.

### P2-05 — combine 단위 경고, boolean 승격, 단일 게이트

**Intent**: spec D4·D5. "검증 통과 = 실행 가능".

**Acceptance**

- compile 서비스가 연결된 equity 어댑터의 필드 메타데이터를 `validate_strategy`에 넘긴다.
  `field_missing`이 compile에서 난다. 어댑터가 없으면 지금과 같다.
- 팩터 출력이 boolean이면 canonical 그래프 끝에 0/1 승격 노드가 붙는다(hydrate 단계, 사용자 문서는
  불변). scalar 출력은 `strategy.factor.output_type` error(compile). `_reject_non_numeric_factor_outputs`가
  compile 통과 문서에서 절대 발화하지 않는 property 테스트(fixture 전수 + 임의 steps 생성).
- `combine.method: raw_weighted`이고 팩터 단위가 다르면 `strategy.combine.unit_mismatch` warning.
- 어댑터 capability(`GROUP_SERIES` 제공 여부)를 compile이 조회해 `neutralize`·`group_rank`가
  `strategy.operator.unsupported` error. 연산자 카탈로그 응답의 `availability`도 같은 capability로.

### P2-06 — filter 횡단면 규칙, `weight.by` 팩터 참조, 죽은 kind 정리

**Intent**: 아이디어 4(거래대금 상위 20%)·5(변동성 역가중)를 언어가 표현하게 한다.

**Acceptance**

- `filter[].top_percent`·`top_count`가 기준일 횡단면에서 적용된다(컴파일러). `min`·`max`와 함께 쓰면
  AND.
- `weight.by`가 팩터 id를 받는다(`x-reference: factor`). 참조 팩터의 출력으로 역가중. 없는 id는
  `strategy.weight.by_missing`.
- `group` 노드(nodes 표기)와 `neutralize`·`group_rank` 단계는 스키마에 남기되 P2-05의 unsupported
  판정으로 실데이터에서 막힌다. duckdb 어댑터가 `GROUP_SERIES`를 제공하는 최소 구현(섹터 코드 필드
  1개)을 이 PR에서 시도하고, 범위를 넘으면 PLAN 변경 기록에 남기고 unsupported 상태로 둔다.
- fixture `ideas/*.yaml` 5개(spec 5절)가 hydrate·validate·preview까지 통과(backend 수준 완료 정의).

### P2-07 — 1.1 → 2.0 업그레이더, upgrade 응답 `environment`, 동결 읽기, OpenAPI

**Intent**: spec D8. 1.1 이력이 동결되고 업그레이드 경로가 열린다.

**Acceptance**

- `_upgrade.py` `UPGRADE_STEPS`에 1.1 → 2.0 step(spec D8 변환 목록). 1.0 문서는 두 판을 순서대로.
  dict 경로·source 경로(ruamel) 같은 step, drift fail-closed.
- `upgrade_document(tree) -> (tree, environment)`: 제거된 `data`·`execution`·`missing_policy`를
  `RunEnvironment`로 돌려준다. `POST /strategy-documents/upgrade` 응답에 `environment`.
- repository codec이 1.0·1.1 row를 업그레이드해 읽고 무결성 검증 3종(1.1 spec D2 방식). saved-reference
  backtest 422. `is_frozen_schema_version` 변경 없음(테스트로 고정).
- golden: `quality_momentum.v1_1.commented.yaml` → 2.0 원문(주석·순서 보존) → tree가 dict 경로와
  동일. 업그레이드된 문서의 preview 결과가 1.1 결과와 같다(`raw_weighted` 보존).
- OpenAPI 재생성. `database/tests` 계약 확인.

**Phase 2 exit**

- [ ] 2.0 fixture(steps·nodes·json·legacy·minimal) 같은 hash. 1.1 fixture 전부 업그레이드 통과.
- [ ] compile 통과 문서가 preview에서 422 없음(property).
- [ ] `ideas/*.yaml` 5개 backend 통과.
- [ ] SoT·책임분리 점검 서브에이전트 blocking 0. SoT 행 "실행 설정"·"연산자 정의"·"steps 컴파일" 채움.

---

## 6. Phase 3 — frontend 2.0 적응

### P3-01 — SDK 2.0, pointer 헬퍼·outline·snippet·Form projection·plan·debugger 적응

**Acceptance**

- `npm run api:generate` 후 diff 0. `/data/*`·`/eligibility/*`·`/signal/*`·`/portfolio/*`·`/risk/*`·
  `/execution/*` pointer 참조가 소스·테스트에서 사라진다(grep 0건 테스트).
- Form이 `select`·`weight`의 `method` union을 `parameters[].kind`와 같은 분기 방식으로 그린다.
  `steps` 항목은 Form에서 "팩터 화면에서 편집합니다"(P5 전까지 링크만).
- outline·snippet 카탈로그(팩터 preset은 튜토리얼 전용으로 강등: 목록에 남기되 "예시" 그룹)·
  execution plan·graph·debugger가 2.0 pointer로.
- e2e fixture는 P3-03. 단위 테스트 전부 green.

### P3-02 — 실행 설정 패널 확장, 1.1 업그레이드 배너, 게이트 통합 표시

**Acceptance**

- 실행 설정 패널(`backtest-run-settings.tsx`)에 시장·빈도·기간·유니버스·체결·수수료·참여율·슬리피지·
  결측 처리. 기본값은 runtime schema `RunEnvironment`에서. 마지막 사용값은 전략별 `localStorage`.
  유니버스는 기존 catalog picker.
- 기간이 전략 문서에서 오던 `dateRange` 의존 제거. OOS 창 검증은 실행 설정의 기간으로.
- 업그레이드 배너가 1.1 문서에도 뜨고, 응답의 `environment`로 실행 설정을 채운다(사용자 확인 후).
- "검증 통과"가 곧 실행 가능임을 UI가 전제한다: 백테스트 버튼 차단 사유에서 `factor-plan` 분기가
  compile error로 흡수되는지 확인(남으면 결함으로 기록).
- IDE 상단에 실행 설정 요약 띠(시안 1의 회색 띠). 문서 밖임을 문구로.

### P3-03 — e2e fixture 2.0, 매뉴얼·README 2.0

**Acceptance**

- e2e 골든을 2.0 steps 문서로. "1.1 revision 열기 → 업그레이드 → 실행 설정 채워짐 → 저장 →
  백테스트" 시나리오 추가.
- 매뉴얼 1절 샘플을 2.0으로, 실행 설정 절 신설, "이 전략을 사람 말로" 표 갱신. README·frontend
  README·`backend/FACTORS.md` 2.0.
- CI `frontend`·`browser-e2e` green(P2 스택의 exit 조건 해소).

**Phase 3 exit**

- [ ] CI 전체 green.
- [ ] 실행 설정이 revision hash에 영향 없음(같은 전략, 다른 기간 → 같은 spec_hash) e2e.
- [ ] SoT·책임분리 점검 blocking 0.

---

## 7. Phase 4 — 파이프라인 캔버스

### P4-01 — `pipeline-projection.ts`

**Acceptance**

- runtime schema × parse tree × compile 진단 → 4단계 모델(거른다 `filter`, 점수를 매긴다 `factors`,
  합쳐서 고른다 `combine`+`select`, 비중을 준다 `weight`). 각 카드는 pointer, 한글 라벨(i18n 키),
  현재 값(작성 여부), 컨트롤 종류(`form-projection`의 필드 행 재사용), 진단 목록.
- 팩터 카드 요약 문장 생성: `steps`를 "종가 → 252일 모멘텀(21일 제외) → 순위"로 (연산자 카탈로그의
  i18n 이름 + 파라미터). `nodes` 표기 팩터는 "노드 N개 · 고급".
- 스키마 fixture만으로 유도되는 단위 테스트. 프론트에 필드 목록을 손으로 적지 않는다.

### P4-02 — 단계 카드 UI(거른다·합쳐서 고른다·비중을 준다)와 실행 설정 띠

**Acceptance**

- `pipeline-panel.tsx`: 4열 캔버스. filter 규칙 추가·삭제(`insertItem`·`remove`), 조건 컨트롤(이상·
  이하·상위 %·상위 N개 → `min`·`max`·`top_percent`·`top_count`), select·weight method 전환(union 분기),
  값 편집은 Form 컨트롤 재사용. combine 카드에 단위 경고 문장(P2-05 warning) 인라인.
- 실행 설정 띠(P3-02)가 캔버스 위. "여기 없는 것" 안내 카드.
- 키보드 조작·ARIA(`frontend-ui-quality.md`).

### P4-03 — 팩터 카드, 빈 팩터 추가, 기준일 미리보기 패널

**Acceptance**

- 팩터 카드: 이름(`id` 편집 = rename, 참조 갱신), 방향, 비중 슬라이더(blur 커밋), 요약 문장,
  "레시피 열기"(P5 전까지 Form의 해당 항목으로), 카드 위 진단.
- "+ 팩터 추가"가 `{ id: factor_<n>, direction: high, steps: [] }`를 `insertItem`(빈 steps는
  semantic error "첫 단계를 추가하세요"로 카드에 표시).
- 미리보기 패널: 기준일 입력, 기존 trace API로 유니버스·필터 통과·결측 제외 수와 상위 N 종목·
  합산 점수(막대). 편집 후 compile ok에서만 갱신, stale 배지.

### P4-04 — IDE 탭 재편, 기본 탭 전환, 빈 화면 e2e

**Acceptance**

- 탭 순서 파이프라인 · 팩터 · 고급(노드 그래프) · YAML 원문 · 이력. 새 전략과 revision 열기의 기본
  탭은 파이프라인. URL search `view` 값에 `pipeline`·`factor` 추가(router `validateSearch`).
- 새 전략 시작 문서 `schema_version: "2.0"\ntitle: ""\n`이 "구조 오류" 없이 열린다(팩터 필요는
  semantic).
- e2e: 빈 문서 → 파이프라인에서 팩터 추가 → (P5 전이므로) Form에서 steps 3단계 입력 → 미리보기 →
  백테스트. 파이프라인 탭 DOM에 `_id`·`_node`·`kind:` 패턴 없음 단언.

**Phase 4 exit**

- [ ] 빈 화면 e2e green.
- [ ] 파이프라인 탭 식별자 0개 단언 green.
- [ ] SoT·책임분리 점검 blocking 0.

---

## 8. Phase 5 — 레시피 빌더

### P5-01 — `recipe-transactions.ts`

**Acceptance**

- 팩터 하나의 `steps` pointer 아래 연산: 단계 추가(끝·중간), 삭제, 위·아래 이동(`remove` +
  `insertItem`을 `planSourceOperations`로 한 undo 단계), 파라미터 편집(`replaceScalar`), 연산자 교체
  (단계 항목 통째 교체), `inputs` 원소 편집(field·상수·prev·중첩 steps 진입).
- property test: 임의 steps 문서·임의 연산에 대해 tree 동치·범위 밖 바이트 보존.
- 첫 단계가 소스가 아니게 되는 연산은 거부하고 이유 반환.

### P5-02 — 팔레트, 단계 카드 UI, 설명, 인라인 진단

**Acceptance**

- `recipe-panel.tsx`: 팩터 헤더(이름·방향·비중), 단계 카드(번호, 한글 이름, kind 그룹 문구, 한 줄
  설명, 파라미터 컨트롤, 위·아래·삭제), 연결 화살표, 결과 타입·단위 표시.
- 팔레트: 연산자 카탈로그(P1-03) 그룹별(시간축·종목 간·계산·조건·데이터), 검색, `availability:
  unsupported`는 숨김. 클릭 시 선택 단계 뒤에 삽입, 단일 입력이면 즉시 연결.
- 진단이 pointer로 단계 카드에 붙는다. 미리보기 패널(P4-03)이 팩터 단독 결과 모드를 갖는다.
- 파이프라인 팩터 카드의 "레시피 열기"가 이 화면으로.

### P5-03 — (backend) `POST /strategy-documents/expand-steps`

**Acceptance**

- 요청 원문(YAML)과 팩터 pointer → 그 팩터의 `steps`를 같은 의미의 `nodes` 원문으로 바꾼 전체
  원문 반환(ruamel round-trip, 주석·순서 보존). dict 경로 컴파일(P2-04)과 tree 동치 drift 검사
  fail-closed. JSON 문서는 422.
- 응답에 변환 뒤 compile 결과(진단·spec_hash)를 함께. hash는 변환 전과 같아야 한다(단언).

### P5-04 — 팩터 결과 미리보기·결측 표시·고급 전환·아이디어 5개 e2e

**Acceptance**

- 결과 미리보기: 값 있는 종목 수, 결측 처리 선택(실행 설정 기본 vs 팩터별 `missing`), 상위 5.
- "고급: 노드 그래프로 보기"가 P5-03 응답을 편집기 범위 교체 한 번으로 적용하고 고급 탭으로 이동.
  되돌리기 한 단계.
- e2e 5건(spec 5절 아이디어): 빈 문서에서 파이프라인·팩터 탭만으로 완성 → 백테스트. 결과 원문이
  `fixtures/strategy_documents/ideas/*.yaml`과 같은 hash. 두 탭 DOM 식별자 0개 단언.

**Phase 5 exit**

- [ ] 아이디어 5개 e2e green (initiative 완료 정의 1·2·4 충족).
- [ ] SoT·책임분리 점검 blocking 0.

---

## 9. Phase 6 — 고급 노드 캔버스

### P6-01 — 그래프 라이브러리 ADR과 스파이크

**Acceptance**

- `docs/superpowers/specs/2026-XX-XX-graph-canvas-adr.md`: 후보(react-flow + dagre, elkjs, 자체 SVG)
  비교 — 번들 크기, 키보드·스크린리더 조작(`frontend-ui-quality.md` 완료 조건), 라이선스, 좌표
  local state 유지. `docs/superpowers/spikes/`에 스파이크 결과.
- 결정: 하나. 기존 `factor-graph-panel`의 DAG 카드 스트립을 대체하는지 병존하는지 명시.

### P6-02 — 노드 캔버스: 드래그 배선, 좌표 local state, 자동 정렬

**Acceptance**

- 노드 팔레트(연산자 카탈로그), 캔버스 배치(자동 정렬 = 레이아웃 엔진, 수동 이동은 local state),
  포트 드래그 배선 = `*_node_id` `replaceScalar`, 노드 추가 = `addNode`, 삭제 가드 유지, 복제(`insertItem`
  복사 + 새 id).
- 키보드: 방향키 포커스 이동, Enter 속성, Delete 삭제, 포트 연결 키보드 경로.
- 인스펙터: 한글 이름·설명·파라미터, 접힌 "식별자(YAML)" 영역에만 node_id·kind.

### P6-03 — 노드 위 진단, 마감 문서

**Acceptance**

- P1-05의 node_id 포함 진단이 캔버스 노드 배지로. 미연결 노드 표시.
- SoT 행(그래프 좌표·캔버스 편집 owner), 로드맵 M8 체크, 매뉴얼 고급 절, ADR 최종 반영.
- 1.1 initiative의 `factor-graph-editor`(목록형)가 대체되면 제거, 병존이면 "고급" 안 접힌 영역.

**Phase 6 exit**

- [ ] 드래그 배선 → YAML 반영 → 되돌리기 e2e.
- [ ] SoT·책임분리 점검 blocking 0. initiative 완료 정의 6항 전부 PLAN에 기록.
