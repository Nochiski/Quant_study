# schema 1.2(실행 설정 분리) · 그래프 표현 구현 워크플로우

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` 또는
> `superpowers:executing-plans`로 PR 단위로 실행한다. PR마다 [YAML Strategy Workbench WORKFLOW
> 13절](../strategy-workbench-yaml-ui/WORKFLOW.md) 절차(scope packet → self-check → diff freeze →
> Opus reviewer 1명 → 재검토 → APPROVE → merge)를 지킨다. 진행 상태는 [PLAN.md](./PLAN.md)만
> 갱신한다. Step 단위 절차는 PR 착수 시 `현재 작업 Packet`에 적는다.

**Goal:** 기존 YAML 문법을 유지한 채 실행 환경(시장·기간·유니버스·수수료·체결·결측)을 UI로
편입하고(schema 1.2), 표현을 YAML과 그래프 둘로 고정해서 비전공자가 그래프 탭만으로 전략을
만들어 백테스트까지 도달하게 한다. 측정은 퀀트 아이디어 5개다.

**Architecture:** backend가 1.2 모델·업그레이더·연산자 카탈로그·`RunEnvironment`를 한 벌 소유한다.
frontend는 runtime schema와 parse tree로 그래프 표현 세 수준(파이프라인·레시피·고급)을 그리고,
모든 GUI 동작을 기존 source 트랜잭션(`planSourceOperation` → `replaceRange` 한 번)으로 번역한다.
배관(kind·node_id·output_node_id)은 언어에 남고 그래프 UI가 숨긴다. spec·hash·검증은 계속 backend
compile만 믿는다. 계약은
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
     └─ P2-01 ─ P2-02 ─ P2-03 ─ P2-04 ─ P2-05          backend schema 1.2
         └─ P3-01 ─ P3-02 ─ P3-03                    frontend 1.2 적응
             └─ P4-01 ─ P4-02 ─ P4-03 ─ P4-04        그래프 1수준: 파이프라인
                 └─ P5-01 ─ P5-02 ─ P5-03            그래프 2수준: 레시피
                     └─ P6-01 ─ P6-02 ─ P6-03        그래프 3수준: 고급 노드 캔버스
```

- 브랜치 이름은 `feat/lang2-<pr-id 소문자>-<slug>`. 예: `feat/lang2-p2-02-schema-1-2`.
- 각 PR의 base는 직전 PR 브랜치다. P1 스택과 P2 스택은 서로 독립이라 병렬 진행할 수 있다
  (`parallel_window`에 기록). P3-01의 base는 P2-05이며 P1-05가 먼저 merge되어 있어야 한다.
- **generated SDK 규칙(1.1 initiative와 같음)**: P2 backend PR은 `backend/openapi.json`만 재생성하고
  `frontend/src/shared/api/generated`는 건드리지 않는다. SDK 재생성과 frontend 적응은 P3-01이 한
  PR에서 한다. P2 PR은 backend gate만 merge gate로 삼고 CI `frontend`·`browser-e2e` job은 P3-01·P3-03의
  exit 조건이다. 각 P2 PR 본문 `제약사항`에 이 사실을 적는다.
- 실 DB 주의: P2-05 merge 전에는 실 SQLite에 1.2 revision을 저장하지 않는다(spec 7절).
- fixture: P2-02부터 `quality_momentum.yaml`이 1.2가 되고 1.1 원본은 `quality_momentum.v1_1.yaml`·
  `.v1_1.commented.yaml`로 보존한다(P2-05 golden).

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
- ADR 2026-09-04 머리말에 개정 링크(D8 대상 사용자). 1.1 spec 머리말에 확장 링크.
- 로드맵 완료 정의 문장을 원문("코드를 몰라도 …")으로 되돌리고 이 initiative 링크.
- `.claude/rules/strategy-workbench-sot.md`에 "실행 설정", "연산자 정의", "그래프 표현 투영" owner
  행 예약(구현 PR이 채운다). "표현은 YAML과 그래프 둘" 문장. 금지 절의 DSL 문장은 유지.

**Non-goal**: 코드 변경.

---

## 4. Phase 1 — 화면 안에서 끝나는 마찰 제거

1.1 계약을 바꾸지 않는다. 전부 1.2 뒤에도 그대로 남는 변경이다. P2와 병렬 가능.

### P1-01 — 문제 목록·검증 배지를 탭과 무관하게

**Intent**: Graph·Form 탭에서 편집 결과를 보려고 YAML 탭으로 돌아가는 왕복을 없앤다.

**Acceptance**

- `DiagnosticsPanel`과 문서 상태 배지("검증 통과"·"구조 오류"·STALE)가 `SourceEditor` 밖(IDE 편집
  영역 하단·툴바)으로 올라가 모든 탭에서 보인다.
- 문제 행 클릭 시 현재 탭이 YAML이면 줄로, 아니면 해당 pointer의 카드·필드로 이동(기존
  `onSelectPointer`). pointer가 현재 탭에 없으면 YAML 탭으로 전환.
- e2e `workbench.workflow.spec.ts` 그래프 시나리오에서 YAML 탭 전환 없이 "검증 통과"를 단언한다.

**예상 파일**: `widgets/strategy-ide/ui/strategy-ide.tsx`, `features/edit-strategy/ui/source-editor.tsx`,
`diagnostics-panel.tsx`, `document-toolbar.tsx`, e2e 1.

### P1-02 — 되돌리기·다시 실행 버튼과 전역 단축키

**Acceptance**

- 툴바에 되돌리기·다시 실행 버튼. CodeMirror undo/redo 깊이를 읽어 비활성 판정.
- Ctrl+Z / Ctrl+Shift+Z(⌘)가 전역 단축키 처리기(`strategy-ide.tsx:280-316`)에 추가되어 편집기가
  hidden인 탭에서도 동작. 입력 필드에 포커스가 있으면 브라우저 기본 동작을 우선.
- Form·Graph 트랜잭션 한 번 = undo 한 단계(기존 불변식) 회귀 테스트.

**예상 파일**: `shared/ui/code-editor/*`(handle에 undo/redo/depth), `document-toolbar.tsx`,
`strategy-ide.tsx`, `shared/config/messages.ts`.

### P1-03 — 연산자 카탈로그(backend)와 노드·필드 한글 이름·설명

**Intent**: 화면 어휘를 사람 말로 바꾼다. 문장은 i18n, 키와 목록은 backend가 소유한다(spec D8).

**Acceptance**

- `domain/factor/_operators.py`에 `OperatorDefinition` 레지스트리(연산자 18개: kind, arity, params,
  output_type_rule, unit_rule, availability, description_key, formula_key, example). 스키마 enum과
  레지스트리 키가 같다는 테스트.
- `GET /api/v1/strategy-documents/operators`. runtime schema 노드 `$defs`의 `title`을 파이썬
  클래스명 대신 `x-description-key`로 대체하고, 모든 노드 property에 `x-description-key`.
- `messages.ts`에 노드 kind 12, 연산자 18, 노드 property 전부, 최상위 섹션의 한글 이름과 한 줄
  설명. Form·Graph 라벨이 키 대신 이름을 보이고 `<code>` 키는 보조 표기로 내려간다.
- Contract Inspector가 i18n 키 문자열을 본문으로 찍는 경로 제거(누락 시 "설명 없음").

**예상 파일**: backend `domain/factor/_operators.py`(신규), `_schema.py`, `adapters/inbound/http_api/_app.py`,
`openapi.json`; frontend `messages.ts`, `strategy-form-panel.tsx`, `factor-graph-editor.tsx`,
`contract-inspector.tsx`, `schema-navigator.ts`.

**Non-goal**: 그래프 새 화면(P4·P5). 연산자 `availability` 판정(P2-04).

### P1-04 — 연산자 먼저 고르기, 조용한 실패 피드백, 오류 본문 인라인

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

**Phase 1 exit**

- [ ] e2e 그래프 시나리오가 YAML 탭 전환 없이 통과.
- [ ] 노드 property·kind·연산자 설명 커버리지 100%(테스트로 고정).
- [ ] SoT·책임분리 점검 서브에이전트 blocking 0.

---

## 5. Phase 2 — backend schema 1.2

### P2-01 — `RunEnvironment` 모델과 실행 요청 확장(브리지)

**Intent**: 실행 설정을 전략 문서 밖에서 받을 자리를 먼저 만든다. 1.1과 완전 호환.

**Acceptance**

- `domain/backtest/_models.py`에 `RunEnvironment`(spec D6)와 canonical JSON·`environment_hash`.
- `BacktestRunSpec`, portfolio preview 요청, trace 요청이 optional `environment`를 받는다. 없으면
  `spec.data`·`spec.execution`·첫 팩터 `graph.missing_policy`에서 만든다(브리지 함수 하나,
  `application/backtest_run/_environment.py`). 있으면 spec 값보다 우선.
- run manifest에 `environment`와 `environment_hash` 기록. 캐시 키에 포함.
- `backtest_run/_service.py`·`portfolio_design/_service.py`·`_trace_service.py`의 `spec.data.*`·
  `spec.execution.*`·`missing_policy` 직접 참조가 전부 `environment`를 거친다(grep 0건 테스트).
- OpenAPI 재생성. frontend SDK는 건드리지 않는다.

**Non-goal**: StrategySpec 변경.

### P2-02 — StrategySpec 1.2: `data`·`execution`·`missing_policy` 제거, 버전 1.2, fixture·hash golden

**Intent**: spec D3 S1~S3. 기존 문법에서 실행 환경만 뺀다.

**Acceptance**

- `CURRENT_SCHEMA_VERSION = "1.2"`. `StrategySpec`에서 `data`·`execution` 제거, `FactorGraph`에서
  `missing_policy` 제거. `Market`·`DataFrequency`·`ExecutionTiming` enum은 `domain/backtest`로 이동.
- `environment`가 실행 요청의 필수 필드가 된다(P2-01 브리지 제거). 없으면 422
  `backtest_run.environment_required`.
- 최상위 필수 키 `schema_version`·`title`·`factors`. 빈 `factors: []`는 semantic
  `strategy.factor.required`(structural 아님).
- hydrate·schema·validation·explanation·diff·trace·compile 경로 갱신. 1.1 문서는
  `structure.unsupported_schema_version`.
- fixture: `quality_momentum.yaml`(1.2), `.json`, `.legacy.json`, `.minimal.yaml`이 같은 hash. 1.1 원본은
  `quality_momentum.v1_1.yaml`·`.v1_1.commented.yaml`로 보존. hash 알고리즘 golden 불변.
- runtime schema fixture 재생성. OpenAPI 재생성.

**제약**: P2-05 전까지 1.1 row는 읽을 수 없다(테스트 DB만). frontend는 P3-01까지 빨간불.

### P2-03 — 추가 필드: `signal.normalization`, 횡단면 eligibility, `risk.risk_factor_id`, saved_* 제거

**Intent**: spec D3 S4~S7과 D4. 아이디어 2(결합)·4(상위 20%)·5(변동성 역가중)를 언어가 표현하게 한다.

**Acceptance**

- `signal.normalization: none|rank|zscore`(기본 `rank`). 컴파일러(`domain/portfolio/_compiler.py`)가
  결합 전에 적용. `none`이 1.1과 수치 동일한 회귀 테스트, `rank`·`zscore` 수치 테스트.
- `eligibility.rules[].operator`에 `top_percent`·`top_count`. 기준일 횡단면에서 적용. 기존 절대
  규칙과 AND.
- `risk.risk_factor_id`(nullable, `x-reference: factor`). `weighting: risk`에서 `risk_field_id`와 배타
  (`strategy.risk.risk_source_conflict` error, `FIELD_APPLICABILITY`에 행 추가). 참조 팩터 출력으로
  역가중.
- `saved_factor`·`saved_subgraph`를 노드 union·스키마·연산자 카탈로그에서 제거. 실행 경로의 거부
  코드는 삭제.
- `_explanation.py`·semantic diff·contract 설명 갱신. i18n 키 추가는 P3-01.

### P2-04 — 단위 경고, boolean 승격, compile 단일 게이트, 연산자 unsupported

**Intent**: spec D4·D5. "검증 통과 = 실행 가능".

**Acceptance**

- compile 서비스가 연결된 equity 어댑터의 필드 메타데이터를 `validate_strategy`에 넘긴다.
  `field_missing`이 compile에서 난다. 어댑터가 없으면 지금과 같다.
- 팩터 출력이 boolean이면 canonical 그래프 끝에 0/1 승격 노드가 붙는다(hydrate 단계, 사용자 문서
  불변, node_id `__promote_<factor>`). scalar 출력은 `strategy.factor.output_type` error(compile).
  `_reject_non_numeric_factor_outputs`가 compile 통과 문서에서 절대 발화하지 않는 property 테스트.
- `signal.normalization: none`이고 팩터 단위가 다르면 `strategy.signal.unit_mismatch` warning.
- 어댑터 capability(`GROUP_SERIES` 제공 여부)를 compile이 조회해 `group` 노드가
  `strategy.operator.unsupported` error. 연산자 카탈로그 응답의 `availability`도 같은 capability로.
  duckdb 어댑터가 `GROUP_SERIES`(섹터 코드 필드 1개)를 제공하는 최소 구현을 시도하고, 범위를
  넘으면 PLAN 변경 기록에 남기고 unsupported로 둔다.
- fixture `ideas/*.yaml` 5개(spec 5절)가 hydrate·validate·preview까지 통과(backend 수준 완료 정의).
  20일 이평 돌파는 `comparison` 출력이 승격되어 통과해야 한다.

### P2-05 — 1.1 → 1.2 업그레이더, upgrade 응답 `environment`, 동결 읽기, OpenAPI

**Intent**: spec D7. 1.1 이력이 동결되고 업그레이드 경로가 열린다.

**Acceptance**

- `_upgrade.py` `UPGRADE_STEPS`에 1.1 → 1.2 step(spec D7 변환 목록). 1.0 문서는 두 판을 순서대로.
  dict 경로·source 경로(ruamel) 같은 step, drift fail-closed.
- `upgrade_document(tree) -> (tree, environment, warnings)`: 제거된 `data`·`execution`·`missing_policy`를
  `RunEnvironment`로 돌려준다. 팩터별 `missing_policy`가 다르면 첫 값 + warning. `saved_*` 노드는
  `strategy_document.upgrade_unsupported_node` 422.
- `POST /strategy-documents/upgrade` 응답에 `environment`·`warnings`.
- repository codec이 1.0·1.1 row를 업그레이드해 읽고 무결성 검증 3종(1.1 spec D2 방식). saved-reference
  backtest 422. `is_frozen_schema_version` 변경 없음(테스트로 고정).
- golden: `quality_momentum.v1_1.commented.yaml` → 1.2 원문(주석·순서 보존) → tree가 dict 경로와
  동일. 업그레이드된 문서의 preview 결과가 1.1 결과와 같다(`normalization: none` 보존).
- OpenAPI 재생성. `database/tests` 계약 확인.

**Phase 2 exit**

- [ ] 1.2 fixture 같은 hash. 1.1 fixture 전부 업그레이드 통과.
- [ ] compile 통과 문서가 preview에서 422 없음(property).
- [ ] `ideas/*.yaml` 5개 backend 통과.
- [ ] SoT·책임분리 점검 서브에이전트 blocking 0. SoT 행 "실행 설정"·"연산자 정의" 채움.

---

## 6. Phase 3 — frontend 1.2 적응

### P3-01 — SDK 1.2, pointer 헬퍼·outline·snippet·Form projection·plan·debugger 적응

**Acceptance**

- `npm run api:generate` 후 diff 0. `/data/*`·`/execution/*`·`/graph/missing_policy` pointer 참조가
  소스·테스트에서 사라진다(grep 0건 테스트).
- `signal.normalization`·횡단면 eligibility·`risk_factor_id`의 i18n(설명·적용 조건) 추가. Form이 새
  필드를 스키마에서 자동으로 그린다(손으로 적지 않는다).
- outline·snippet 카탈로그(팩터 preset은 "예시" 그룹으로 강등, 튜토리얼 전용)·execution plan·graph·
  debugger가 1.2 pointer로.
- 단위 테스트 전부 green. e2e fixture는 P3-03.

### P3-02 — 실행 설정 패널 확장, 1.1 업그레이드 배너, 실행 설정 띠

**Acceptance**

- 실행 설정 패널(`backtest-run-settings.tsx`)에 시장·빈도·기간·유니버스·체결·수수료·참여율·슬리피지·
  결측 처리. 기본값은 runtime schema `RunEnvironment`에서. 마지막 사용값은 전략별 `localStorage`.
  유니버스는 기존 catalog picker.
- 기간이 전략 문서에서 오던 `dateRange` 의존 제거. OOS 창 검증은 실행 설정의 기간으로.
- 업그레이드 배너가 1.1 문서에도 뜨고, 응답의 `environment`로 실행 설정을 채운다(사용자 확인 후).
  `warnings`를 배너에 표시.
- 백테스트 버튼 차단 사유에서 `factor-plan` 분기가 compile error로 흡수되는지 확인(남으면 결함으로
  기록).
- IDE 상단에 실행 설정 요약 띠(시안 1). 문서 밖임을 문구로.

### P3-03 — e2e fixture 1.2, 매뉴얼·README 1.2

**Acceptance**

- e2e 골든을 1.2 문서로. "1.1 revision 열기 → 업그레이드 → 실행 설정 채워짐 → 저장 → 백테스트"
  시나리오 추가. "같은 전략, 다른 기간 → 같은 spec_hash" 시나리오.
- 매뉴얼 1절 샘플을 1.2로, 실행 설정 절 신설, "이 전략을 사람 말로" 표 갱신. README·frontend
  README·`backend/FACTORS.md` 1.2.
- CI `frontend`·`browser-e2e` green(P2 스택의 exit 조건 해소).

**Phase 3 exit**

- [ ] CI 전체 green.
- [ ] 같은 전략·다른 기간 → 같은 spec_hash e2e.
- [ ] SoT·책임분리 점검 blocking 0.

---

## 7. Phase 4 — 그래프 1수준: 파이프라인

### P4-01 — `pipeline-projection.ts`

**Acceptance**

- runtime schema × parse tree × compile 진단 → 4단계 모델(거른다 `eligibility`, 점수를 매긴다
  `factors`, 합쳐서 고른다 `signal`+`portfolio` 선택·리밸런스 필드, 비중을 준다
  `portfolio.weighting`+`risk`). 각 카드는 pointer, 한글 라벨(i18n 키), 현재 값(작성 여부), 컨트롤
  종류(`form-projection`의 필드 행 재사용), 진단 목록. 필드의 단계 배정은 스키마 `x-stage` 마커로
  backend가 준다(프론트에 필드 목록을 손으로 적지 않는다; backend 변경은 이 PR에 포함, 1.2 계약
  additive).
- 팩터 카드 요약 문장: `recipe-projection`(P5-01 전에는 이 PR에 최소 체인 판정 포함)이 체인이면
  "종가 → 252일 모멘텀(21일 제외) → 순위"(연산자 카탈로그 i18n 이름 + 파라미터), 아니면 "노드 N개 ·
  고급".
- 스키마 fixture만으로 유도되는 단위 테스트.

### P4-02 — 단계 카드 UI(거른다·합쳐서 고른다·비중을 준다)와 실행 설정 띠

**Acceptance**

- `pipeline-panel.tsx`: 4열 캔버스. eligibility 규칙 추가·삭제(`insertItem`·`remove`), 조건 컨트롤
  (이상·이하·상위 %·상위 N개 → `operator`·`value`), 정규화 선택(`signal.normalization`) 카드에 단위
  경고 문장 인라인, 선택·리밸런스·비중·한도 필드는 Form 컨트롤 재사용. 적용 조건 경고는 카드
  안에서 필드를 흐리게.
- 실행 설정 띠(P3-02)가 캔버스 위. "여기 없는 것" 안내 카드.
- 키보드 조작·ARIA(`frontend-ui-quality.md`).

### P4-03 — 팩터 카드, 빈 팩터 추가, 기준일 미리보기 패널

**Acceptance**

- 팩터 카드: 이름(`factor_id` 편집 = 참조 갱신 rename), 방향, 비중 슬라이더(blur 커밋), 요약 문장,
  "레시피 열기"(P5 전까지 기존 Graph 편집기로), 카드 위 진단.
- "+ 팩터 추가"가 `{ factor_id: factor_<n>, direction: high, graph: { nodes: [], output_node_id: "" } }`를
  `insertItem`. 빈 그래프는 semantic error "첫 단계를 추가하세요"로 카드에 표시(backend 메시지).
- 미리보기 패널: 기준일 입력, 기존 trace API로 유니버스·필터 통과·결측 제외 수와 상위 N 종목·
  합산 점수(막대). 편집 후 compile ok에서만 갱신, stale 배지.

### P4-04 — 탭을 그래프·YAML 둘로, 기본 탭 그래프, Form·JSON 은퇴, 빈 화면 e2e

**Acceptance**

- 탭 `그래프` · `YAML`. 새 전략과 revision 열기의 기본 탭은 그래프. URL search `view`에 `graph`·`yaml`만.
  옛 `form`·`json`·`graph`(1.1) 값은 각각 `graph`·`yaml`로 마이그레이션(라우터 `validateSearch`).
- Form 탭 컴포넌트 제거(`strategy-form-panel.tsx`의 `FormFieldsEditor`는 그래프 카드가 재사용하므로
  모듈은 남긴다). JSON 투영 탭 제거(YAML 탭의 `포맷` 명령은 유지). Diff는 revision 영역에 남는다.
- 새 전략 시작 문서 `schema_version: "1.2"\ntitle: ""\n`이 "구조 오류" 없이 열린다.
- e2e: 빈 문서 → 그래프에서 팩터 추가 → (P5 전이므로) 기존 Graph 편집기로 노드 3개 → 미리보기 →
  백테스트. 그래프 탭 파이프라인 수준 DOM에 `_id`·`_node`·`kind:` 패턴 없음 단언.

**Phase 4 exit**

- [ ] 빈 화면 e2e green.
- [ ] 파이프라인 수준 식별자 0개 단언 green.
- [ ] SoT·책임분리 점검 blocking 0.

---

## 8. Phase 5 — 그래프 2수준: 레시피

### P5-01 — `recipe-projection.ts`·`recipe-transactions.ts`

**Acceptance**

- 체인 판정: 팩터 `graph.nodes`가 소스 노드 하나로 시작해 각 노드가 직전 노드만 참조하고
  `output_node_id`가 마지막이면 체인. 다중 입력 노드는 다른 입력이 field/constant/parameter 소스
  노드(체인 밖 잎)일 때만 허용("÷ 시가총액"). 그 외는 비체인 → "고급에서 편집".
- 연산: 단계 추가(끝·중간) = `addNode`(kind·id `<operator>_<n>`·입력=직전·출력=마지막) + 다음
  노드 `input_node_id` 재배선; 삭제 = `remove` + 재배선(+출력 갱신); 이동 = 재배선; 파라미터 =
  `replaceScalar`; 연산자 교체 = 노드 항목 교체(kind 동반). 여러 연산은 `planSourceOperations`로 한
  undo 단계. 결과가 체인 불변식을 깨면 거부하고 이유 반환.
- property test: 임의 체인 문서·임의 연산에 대해 tree 동치·체인 유지·범위 밖 바이트 보존.

### P5-02 — 팔레트, 단계 카드 UI, 설명, 인라인 진단

**Acceptance**

- `recipe-panel.tsx`: 팩터 헤더(이름·방향·비중), 단계 카드(번호, 한글 이름, kind 그룹 문구, 한 줄
  설명, 파라미터 컨트롤, 위·아래·삭제), 연결 화살표, 결과 타입·단위 표시.
- 팔레트: 연산자 카탈로그(P1-03) 그룹별(데이터·시간축·종목 간·계산·조건), 검색, `availability:
  unsupported`는 숨김. 클릭 시 선택 단계 뒤에 삽입, 다중 입력 연산자는 다른 입력을 데이터 필드
  picker로 즉시 묻는다.
- 진단이 pointer로 단계 카드에 붙는다. 비체인 팩터는 "고급으로" 링크만.
- 파이프라인 팩터 카드의 "레시피 열기"가 이 화면으로. 접힌 "식별자(YAML)" 영역에 node_id·kind.

### P5-03 — 팩터 결과 미리보기·결측 표시, 아이디어 5개 e2e

**Acceptance**

- 결과 미리보기: 값 있는 종목 수, 결측 처리(실행 설정 `missing`) 표시, 상위 5.
- e2e 5건(spec 5절 아이디어): 빈 문서에서 그래프 탭(파이프라인·레시피)만으로 완성 → 백테스트.
  결과 원문이 `fixtures/strategy_documents/ideas/*.yaml`과 같은 hash. 두 수준 DOM 식별자 0개 단언.
- 매뉴얼에 그래프 화면 절과 예시 5개(튜토리얼).

**Phase 5 exit**

- [ ] 아이디어 5개 e2e green (initiative 완료 정의 1·2).
- [ ] SoT·책임분리 점검 blocking 0.

---

## 9. Phase 6 — 그래프 3수준: 고급 노드 캔버스

### P6-01 — 그래프 라이브러리 ADR과 스파이크

**Acceptance**

- `docs/superpowers/specs/<date>-graph-canvas-adr.md`: 후보(react-flow + dagre, elkjs, 자체 SVG)
  비교 — 번들 크기, 키보드·스크린리더 조작(`frontend-ui-quality.md` 완료 조건), 라이선스, 좌표
  local state 유지. `docs/superpowers/spikes/`에 스파이크 결과.
- 결정: 하나. 기존 `factor-graph-panel` DAG 카드 스트립과 `factor-graph-editor` 목록형을 대체한다.

### P6-02 — 노드 캔버스: 드래그 배선, 좌표 local state, 자동 정렬

**Acceptance**

- 노드 팔레트(연산자 카탈로그), 캔버스 배치(자동 정렬 = 레이아웃 엔진, 수동 이동은 local state),
  포트 드래그 배선 = `*_node_id` `replaceScalar`, 노드 추가 = `addNode`, 삭제 가드 유지, 복제(`insertItem`
  복사 + 새 id).
- 키보드: 방향키 포커스 이동, Enter 속성, Delete 삭제, 포트 연결 키보드 경로.
- 인스펙터: 한글 이름·설명·파라미터, 접힌 "식별자(YAML)" 영역에만 node_id·kind.
- 레시피 ↔ 고급 전환: 같은 `nodes`라 변환이 없다. 체인이면 레시피가 기본, 아니면 고급이 기본.

### P6-03 — 노드 위 진단, 마감 문서

**Acceptance**

- P1-05의 node_id 포함 진단이 캔버스 노드 배지로. 미연결 노드 표시.
- 옛 `factor-graph-editor`(목록형)·DAG 카드 스트립 제거.
- SoT 행(그래프 좌표·캔버스 편집 owner), 로드맵 M8 체크, 매뉴얼 고급 절, ADR 최종 반영.

**Phase 6 exit**

- [ ] 드래그 배선 → YAML 반영 → 되돌리기 e2e.
- [ ] SoT·책임분리 점검 blocking 0. initiative 완료 정의 6항 전부 PLAN에 기록.
