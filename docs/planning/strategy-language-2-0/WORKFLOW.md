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
     ├─ P1-01 ─ P1-02 ─ P1-03 ─ P1-04 ─ P1-05 ─ P1-06   화면 안 마찰 제거 (1.1 위, 독립. P1-06은 감사 후속 문서)
     └─ P2-01 ─ … ─ P2-09                              backend schema 1.2 (9 PR, 아래 5절)
         └─ P3-01 ─ P3-02 ─ P3-03                    frontend 1.2 적응
             └─ P4-01 ─ P4-02 ─ P4-03 ─ P4-04        그래프 1수준: 파이프라인
                 └─ P5-01 ─ P5-02 ─ P5-03            그래프 2수준: 레시피
                     └─ P6-01 ─ P6-02 ─ P6-03        그래프 3수준: 고급 노드 캔버스
```

- 브랜치 이름은 `feat/lang2-<pr-id 소문자>-<slug>`. 예: `feat/lang2-p2-02-schema-1-2`.
- 각 PR의 base는 직전 PR 브랜치다. P1 스택과 P2 스택은 서로 독립이라 병렬 진행할 수 있다
  (`parallel_window`에 기록). 교차 제약 두 가지: **P2-06·P2-07은 P1-03(연산자 카탈로그)이 merge된 뒤
  착수한다**(두 PR이 카탈로그의 `saved_*` 제거·`availability`를 건드린다). P3-01의 base는 P2-09이며
  P1 스택 끝(P1-06)이 먼저 merge되어 있어야 한다.
- **generated SDK 규칙(P2-01에서 개정)**: OpenAPI를 바꾸는 backend PR은 같은 PR에서
  `frontend/src/shared/api/generated`도 재생성해 별도 커밋으로 넣는다. CI `frontend` job이
  `npm run api:generate` 뒤 `git diff --exit-code -- ../backend/openapi.json src/shared/api/generated`를
  돌리므로, 생성 파일을 빼면 그 PR이 곧바로 빨간불이 된다. 커밋에는 **생성 산출물만** 넣고 소비자
  배선(실행 설정 패널·요청 본문 연결)은 P3-01 그대로다. 재생성 뒤 `npm run typecheck`·`lint`·
  `test`·`build`를 돌려 결과를 PR 본문에 적는다. `browser-e2e` job은 계속 P3-03의 exit 조건이다.
- 실 DB 주의: P2-09 merge 전에는 실 SQLite에 1.2 revision을 저장하지 않는다(spec 7절).
- fixture 이름과 역할(spec D7): P2-03부터 `quality_momentum.yaml`이 1.2가 되고, 1.1 원본은
  `quality_momentum.v1_1.yaml`로 복사해 보존한다(1.1 → 1.2 업그레이드 입력). 기존
  `quality_momentum.v1_1.commented.yaml`은 **건드리지 않는다** — 이 파일은 1.0 → 1.1 source
  업그레이드의 기대 출력이고 테스트 세 곳이 단언한다. 1.0 문서의 최종 기대 출력은 P2-09가 만드는
  `quality_momentum.v1_2.commented.yaml`이다.

## 2. 공통 gate

| PR 유형 | self-check |
|---|---|
| backend | focused pytest → `uv run pytest` 전체 → `uv run ruff check src tests` → `uv run pyright` → 계약(모델·facade 이름)이 바뀌면 루트에서 `uv run --project backend pytest database/tests -q` |
| API contract | backend 전체 → `uv run python scripts/export_openapi.py openapi.json` → diff 확인 (P3-01부터 `npm run api:generate` 포함) → frontend 전체 |
| frontend | focused vitest → `npm run typecheck` → `npm run lint` → `npm test` → `npm run build` |
| E2E 포함 | 위 + `npm run test:e2e` |

runtime schema fixture는 backend 모델이 바뀔 때마다 `uv run python tools/export_runtime_schema.py`로
재생성한다.

PR 크기는 [YAML Strategy Workbench WORKFLOW 12절](../strategy-workbench-yaml-ui/WORKFLOW.md)을
따른다(handwritten diff 150~450줄 권장, logical file 8개 이내, 600줄·10파일 초과 시 분할이 기본).
**분할이 불가능해 상한을 넘기면 그 PR 본문 `제약사항`에 사유를 한 줄로 적는다.** 12절이 요구하는
기록이며, 생략하면 reviewer가 크기를 결함으로 본다.

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
- `.claude/rules/strategy-workbench-sot.md`(spec D11 표와 같은 목록):
  - "실행 설정", "연산자 정의", "그래프 표현 투영" owner 행 예약. owner는 실제 파일을 적고 미구현은
    괄호로 표시한다. 그래프 투영 행에는 "필드 → 단계 배정은 runtime schema `x-stage`(backend)".
  - "전략 의미" 행에 "표현은 YAML과 그래프 둘" 문장.
  - 업그레이드 행 제목을 "1.0 → 1.1 → 1.2 문서 업그레이드 변환(1.2 step은 P2-09에서 추가, 아직
    없음)"으로 예약하고, 같은 행의 `LEGACY_SCHEMA_VERSION`(단수) 표현을 버전 디스패치 API 예약
    문구로 바꾼다.
  - 금지 절 캐시 키 문장에 `environment_hash` 추가. DSL 문장은 유지.
  - `paths:` frontmatter에 `docs/planning/strategy-language-2-0/**`,
    `docs/planning/strategy-gui-editing/**`,
    `docs/superpowers/specs/2026-09-20-strategy-language-2-0-and-pipeline-canvas-design.md` 추가.

**Non-goal**: 코드 변경.

**제약사항(12절 예외 기록)**

- 이 PR은 9파일 +1400줄대로 12절 상한(600줄·10파일)을 넘는다. 기획 패키지 3문서와 설계 spec은
  서로를 참조해서 한 벌로만 리뷰할 수 있고, 쪼개면 중간 커밋의 링크가 깨진다.
- `tools/update-plan-progress.ps1`은 `strategy-gui-editing` 판의 337줄 중 phase 이름 해시테이블만
  다른 세 번째 복제본이다. 공용 위치(`docs/planning/tools/`)로 올리고 패키지별 phase 맵을 인자로
  받는 정리는 이 initiative 범위 밖이라 **backlog**로 남긴다(P0-01 리뷰 P3 finding).

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

- `domain/factor/_operators.py`에 `OperatorDefinition` 레지스트리(kind, arity, params,
  output_type_rule, unit_rule, availability, description_key, formula_key, example). **키는
  `(kind, operator)` 쌍이고 항목은 23개다**(unary 2·binary 4·time_series 6·cross_sectional 4·group 2·
  comparison 5). `rank`가 `cross_sectional`·`group` 양쪽에 있어 이름 단독 키는 충돌하고,
  `FactorComparisonOperator` 5개도 팔레트·설명 대상이라 뺄 수 없다. 스키마 enum 6종의 값 전부가
  레지스트리 키로 있다는 테스트(양방향 전수).
- `GET /api/v1/strategy-documents/operators`. runtime schema 노드 `$defs`의 `title`을 파이썬
  클래스명 대신 `x-description-key`로 대체하고, 모든 노드 property에 `x-description-key`.
- `messages.ts`에 노드 kind 12, 연산자 23, 노드 property 전부, 최상위 섹션의 한글 이름과 한 줄
  설명. Form·Graph 라벨이 키 대신 이름을 보이고 `<code>` 키는 보조 표기로 내려간다.
- Contract Inspector가 i18n 키 문자열을 본문으로 찍는 경로 제거(누락 시 "설명 없음").

**예상 파일**: backend `domain/factor/_operators.py`(신규), `_schema.py`, `adapters/inbound/http_api/_app.py`,
`openapi.json`; frontend `messages.ts`, `strategy-form-panel.tsx`, `factor-graph-editor.tsx`,
`contract-inspector.tsx`, `schema-navigator.ts`.

**Non-goal**: 그래프 새 화면(P4·P5). 연산자 `availability` 판정(P2-07).

### P1-04 — 연산자 먼저 고르기, 조용한 실패 피드백, 오류 본문 인라인

**Acceptance**

- Graph 편집기의 "노드 추가"가 kind 드롭다운 대신 연산자 목록(P1-03 카탈로그, 그룹: 데이터·시간축·
  종목 간·계산·조건)을 보이고 kind는 `addNode`가 채운다. `nodeKinds` 파생 로직은 유지하되 UI에서
  숨긴다.
- `availability !== "available"`인 연산자(현재 `group` 2개)는 목록에서 비활성으로 내리거나 설명 끝에
  "아직 지원되지 않습니다"를 붙인다. P1-03이 이름·설명을 붙여 놓아 지금은 실행되지 않는 경로로
  사용자를 더 적극적으로 안내한다(P1-03 1차 리뷰 P3). 실제 capability 판정은 P2-07이다.
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

### P1-06 — Phase 1 감사 후속(문서)

**Intent**: Phase 1 종료 감사(2026-09-21)가 exit (c)를 BLOCKING 2건으로 막았다. SoT 대장 충돌
표식(DEFECT-P1X-001)은 P1-02 `ac3a3d0e`와 P1-03~P1-05 cascade rebase로 해소됐다. 남은 PLAN 기록
결함(DEFECT-P1X-002)과 비차단 SoT 문장·이월 담당을 코드 변경 없이 문서 PR 하나로 닫는다. 감사가
권고로 신설한 PR이라 계획 PR 수가 28에서 29가 된다.

**Acceptance**

- PLAN P1 행이 실제 상태(PR 링크, 리뷰 회차·결과, 수정 커밋)를 담고, Review 기록 표에 P1-01·P1-05
  전 회차 행이 있다. P1-02 1·2차 행의 수치가 서로 모순되지 않는다.
- `관찰 backlog` 절에는 BACKLOG 항목만, 변경 기록은 `변경 기록` 절에만 있다.
- 감사 비차단 N1·N2·N3·N4·N5·N10과 P6-03 키보드 flake에 `담당:` PR이 있고, 그 PR의 acceptance에
  같은 BACKLOG 번호로 한 줄씩 예약돼 있다(BACKLOG-001 선례).
- SoT 대장: 정수 하한 행이 두 소비자의 읽기 방식을 사실대로 적고(N8), e2e 잠금·포트·빌드 주소 행이
  실제 owner 파일을 가리킨다(N9).
- `update-plan-progress.ps1 -Check`와 충돌 표식 검사 통과.

**Non-goal**: 코드 변경. 매뉴얼 재촬영은 담당만 정한다(BACKLOG-002, P3-03).

**Phase 1 exit**

- [ ] e2e 그래프 시나리오가 YAML 탭 전환 없이 통과.
- [ ] 노드 property·kind·연산자 설명 커버리지 100%(테스트로 고정).
- [ ] SoT·책임분리 점검 서브에이전트 blocking 0.

---

## 5. Phase 2 — backend schema 1.2

9 PR이다. P0-01 리뷰에서 "P2-02 하나가 `spec.data`·`spec.execution`·`missing_policy`를 참조하는
backend 소스 13개에 걸쳐 12절 크기 규칙을 지킬 수 없다"가 blocking으로 지적되어, 사용자에게 설명
가능한 동작 또는 invariant 하나씩으로 쪼갰다.

### P2-01 — `RunEnvironment` 모델, 브리지, 실행 요청 optional `environment`, 실행 설정 스키마

**Intent**: 실행 설정을 전략 문서 밖에서 받을 자리를 먼저 만든다. 1.1과 완전 호환.

**Acceptance**

- `domain/backtest/_models.py`에 `RunEnvironment`(spec D6)와 canonical JSON·`environment_hash`.
- **enum(`Market`·`DataFrequency`·`ExecutionTiming`)을 이 PR에서 옮기지 않는다.** `RunEnvironment`는
  이미 선언된 `domain.backtest → domain.strategy` 화살표로 현재 위치의 enum을 그대로 읽는다. 옮기고
  `domain/strategy`가 re-export하면 `domain.strategy → domain.backtest` 화살표가 생겨 순환이 되고
  (`tests/architecture/test_dependency_direction.py` 실패), 경계 규칙이 facade `__init__` 재수출
  자체를 금지한다. 이동은 `DataStep`·`ExecutionStep`이 사라지는 P2-03에서 한다.
- 브리지는 `domain/backtest`가 소유한다: `environment_from_legacy_spec(spec) -> RunEnvironment`.
  `application/backtest_run`에 두지 않는다 — `portfolio_design → backtest_run` 화살표가 기존
  `backtest_run → portfolio_design`과 순환을 만들어 `tests/architecture/test_dependency_direction.py`가
  실패하고, 경계 규칙상 application → application은 outgoing port 소비에만 허용된다(브리지는 로직).
- `DEPENDS_ON` 갱신(새로 필요한 것만): `application.portfolio_design`·
  `application.strategy_authoring`에 `domain.backtest` 추가, `domain.backtest`에 `domain.factor`
  추가(`MissingPolicy`). `application.backtest_run`의 `domain.backtest`와 `domain.backtest`의
  `domain.strategy`는 **이미 선언되어 있다**. 아키텍처 테스트 green.
- `BacktestRunSpec`, portfolio preview 요청, trace 요청이 optional `environment`를 받는다. 없으면
  브리지로 만든다. 있으면 spec 값보다 우선.
- run manifest에 `environment`와 `environment_hash` 기록. 실행 결과 캐시 키에 포함.
- `backtest_run/_service.py`·`portfolio_design/_service.py`·`_trace_service.py`의 `spec.data.*`·
  `spec.execution.*` 직접 참조가 전부 `environment`를 거친다(grep 0건 테스트).
- **실행 설정 스키마 엔드포인트** `GET /api/v1/run-environments/schema`: `RunEnvironment` dataclass에서
  생성한 JSON Schema(필드별 타입·기본값·enum). 기존 `strategy_document_schema()` 빌더를 재사용하되
  전략 authoring runtime schema와는 **다른 산출물**이다(owner가 `domain/backtest`). 프론트가 실행
  설정 패널 기본값을 손으로 적지 않게 하는 경로다(P3-02가 소비).
- OpenAPI 재생성과 생성 SDK 재생성(1절 generated SDK 규칙). 소비자 배선은 P3-01 그대로.

**Non-goal**: StrategySpec 변경. `missing_policy`(P2-02). enum 물리 이동(P2-03).

### P2-02 — `graph.missing_policy` 제거 → `environment.missing`(plan 인자, `plan_hash` 유지)

**Intent**: spec D3 S3와 D6. 결측 정책이 팩터 그래프에서 실행 설정으로 옮겨가되 **plan의 일부로
남는다**.

**Acceptance**

- `build_factor_execution_plan`(실제 이름 `compile_factor_plan`)이 `missing: MissingPolicy`를
  **인자로** 받는다. 호출자가 `environment.missing`을 넘긴다.
- **`FactorGraph.missing_policy` 필드 자체는 이 PR에서 지우지 않는다**(구현 시 결정, 1). 이 시점의
  `CURRENT_SCHEMA_VERSION`은 아직 `"1.1"`이라 필드를 지우면 1.1 문서가 `structure.unknown_field`로
  깨지고, 1.0 → 1.1 업그레이드 출력(`quality_momentum.v1_1.commented.yaml`, 이 패키지가 "건드리지
  않는다"고 못 박은 fixture)이 곧바로 compile 실패가 된다. 대신 1.1 호환 입력으로 남기고 runtime
  schema·`FieldContract`에 `x-deprecated`를 실어 화면 어휘에서 뺄 수 있게 한다. 물리 삭제는
  `CURRENT_SCHEMA_VERSION` 1.2와 함께 오는 P2-03이다.
- `domain/factor/_planning.py:122-131`의 plan payload에 결측 정책이 그대로 남아 `plan_hash`가 계속
  갈린다. `build_factor_matrix_cache_key`(`:153-160`)는 무변경. 회귀 테스트: 같은 전략을
  `missing: drop`과 `missing: zero`로 계획하면 `plan_hash`가 **다르다**.
- 소비자 갱신: `domain/factor`의 `_planning.py`·`_evaluation.py`(`:192`, `:270-285`)·`_registry.py`
  (`:49`, `:619`), `application/portfolio_design/_service.py`, `application/backtest_run/_service.py`.
  `graph.missing_policy` 참조 grep 0건 테스트.
- P2-01 브리지가 `첫 팩터 graph.missing_policy`를 읽던 부분은 이 PR 이후 의미가 없어지므로, 1.1
  문서에서 만들 때만 쓰는 legacy 입력으로 좁힌다. **팩터별 값이 서로 다르면 대표값 하나를 고르지
  않고 거부한다**(구현 시 결정, 2): 실행 설정은 단일 값이라 대표값을 고르면 팩터 일부가 조용히
  다른 결측 처리로 계산된다. 진단 코드는 `run_environment.missing_policy_conflict`이고 메시지가
  "`environment`를 명시하면 문서 값을 읽지 않으므로 통과한다"는 우회 경로를 안내한다. spec D7의
  업그레이더 규칙("첫 팩터 값 + warning")은 P2-09 그대로다.
- **팩터 sandbox 요청도 결측 정책을 갖는다**(구현 시 결정, 3): `FactorGraphRequest`·
  `FactorPreviewRequest`의 `missing: MissingPolicy | None = None`. `None`이면 브리지의
  `resolve_graph_missing_policy`가 그래프의 1.1 값으로 떨어뜨려, 실행 설정이 없는
  `/factors/explain`·`/factors/preview`가 실제 실행과 같은 결측 정책·`plan_hash`를 낸다. 기본값을
  `DROP`으로 고정하면 편집 화면의 실행 플랜 패널이 실제 실행과 갈린다(P2-02 리뷰 P1).
- runtime schema fixture 재생성, `export_openapi.py`로 `backend/openapi.json` 재생성과 생성 SDK
  재생성(1절 규칙). `FactorGraph.missing_policy`는 `deprecated: true`가 붙어 남고,
  `FactorDefinition.missing_policy`(팩터 카탈로그의 per-factor 기본값)는 빠진다 — 결측 정책은
  카탈로그가 아니라 실행이 소유한다.

**제약사항**: 캐시 키 회귀가 이 PR의 핵심 invariant다. 분리하지 않으면 P2-03의 대량 삭제에 묻힌다.

### P2-03 — `data`·`execution` 제거, `CURRENT_SCHEMA_VERSION = "1.2"`, 필수 키 2개, fixture·hash golden

**Intent**: spec D3 S1~S2. 기존 문법에서 실행 환경만 뺀다.

**Acceptance**

- `CURRENT_SCHEMA_VERSION = "1.2"`. `StrategySpec`에서 `data`·`execution` 제거.
- `DataStep`·`ExecutionStep`이 사라지는 이 시점에 `Market`·`DataFrequency`·`ExecutionTiming` enum을
  `domain/strategy`에서 `domain/backtest`로 옮긴다. **호환 re-export를 만들지 않는다**(경계 규칙이
  facade 재수출을 금지). 처리는 파일마다 다르다.
  - `domain/strategy/facade/specification.py`: 세 enum의 import(`:36`, `:41`, `:46`)와 `__all__`
    항목(`:70`, `:75`, `:87`)을 **삭제한다.** 이 파일은 소비자가 아니라 재수출 지점이므로
    `domain.backtest`를 읽게 바꾸면 안 된다 — `domain.strategy → domain.backtest` 화살표가 생겨
    기존 반대 방향과 순환이 된다(P2-01에서 막은 것과 같은 실패). `domain/strategy`는 이 enum을 더
    이상 공개하지 않는다.
  - `application/strategy_design/_service.py`: `template()`이 `DataStep(market=Market.KRX, …)`를
    만들 때만 쓰므로 `data` 제거와 함께 **import가 사라진다.** 리다이렉트 대상이 아니다.
  - `adapters/outbound/engine_portfolio/_adapter.py:85`(`ExecutionTiming.NEXT_OPEN`): 실제로
    `domain.backtest`를 새로 읽는 **유일한** 파일이다. 그 facade `DEPENDS_ON`(현재
    `application.portfolio_design`·`domain.portfolio`·`domain.strategy`)에 `domain.backtest`를
    추가한다. adapter → domain이라 순환이 없다.
  - `MissingPolicy`는 `domain/factor`에 그대로 둔다.
- **P2-02가 남긴 1.1 호환 잔재를 이 PR에서 물리 삭제한다**: `FactorGraph.missing_policy` 필드,
  `domain/factor/_nodes.py`의 `DEPRECATED_FIELD` 마커, `domain/strategy/_schema.py`의
  `x-deprecated` 발행과 `FieldContract.deprecated`(다른 deprecated 필드가 생기지 않았다면),
  브리지의 `resolve_graph_missing_policy`·`_missing_from_legacy_graphs`(문서 입력이 사라지면
  `environment.missing`만 남는다). 1.2 문서의 `graph.missing_policy`는 `structure.unknown_field`가
  된다. 1.1 문서에서의 이관은 P2-09 업그레이더가 맡는다.
- **최상위 필수 키는 `schema_version`·`title` 둘**이다. `factors: tuple[FactorSignal, ...] = ()`로
  기본값을 주어 생략도 빈 배열도 `structure.missing_field`를 내지 않고, 두 경우 모두 semantic
  `strategy.factor.required`가 난다. 회귀 테스트: `schema_version: "1.2"\ntitle: ""\n`의 hydrate
  진단에 `structure.*`가 0건이고 `strategy.factor.required` 1건.
- `environment`가 실행 요청의 필수 필드가 된다(P2-01 브리지는 업그레이드 경로 전용으로 축소).
  없으면 422 `backtest_run.environment_required`.
- hydrate·schema·validation·explanation·diff·trace·compile 경로 갱신. 1.1 문서는
  `structure.unsupported_schema_version`.
- 기본 템플릿 빌더 `application/strategy_design/_service.py`의 `template()` 갱신. `StrategySpec`을
  직접 만들면서 `DataStep`을 채우고 있어 `data` 제거로 깨진다. 새 전략 기본 문서의 내용은 P4-04의
  시작 문서 결정(`schema_version`·`title`만)과 같아야 한다.
- fixture: `quality_momentum.yaml`(1.2), `.json`, `.legacy.json`, `.minimal.yaml`이 같은 hash. 1.1
  원본을 `quality_momentum.v1_1.yaml`로 복사해 보존한다. **`quality_momentum.v1_1.commented.yaml`은
  건드리지 않는다**(1.0 → 1.1 기대 출력, 테스트 3곳이 단언 중).
- **P2-01 잔여 두 지점을 `environment`로 이관한다**(그때까지는 명시 실행 설정과 문서 값이 갈린다).
  - `adapters/outbound/engine_portfolio/_adapter.py:42` — `requirements()`가
    `spec.execution.participation_rate < 1.0`으로 `PARTIAL_FILL` 요구를 판정한다. `assess(spec)`·
    `requirements(spec)` 시그니처에 `environment`를 더하고 호출자(`portfolio_design/_service.py`의
    `_prepare`)가 넘긴다. adapter → domain이라 facade `DEPENDS_ON`에 `domain.backtest` 추가로 끝난다
    (enum 이동 항목이 이미 같은 줄을 요구한다). **틀리는 방향은 과소 선언이다**: 문서
    `execution.participation_rate = 1.0` + 명시 `environment.participation_rate = 0.1`이면
    요구 집합에 `PARTIAL_FILL`이 빠져 능력 게이트가 조용히 약해진다(반대 조합은 과다 선언이라
    무해). 회귀 테스트: "문서 1.0 + 환경 0.1이면 `requirements()`에 `PARTIAL_FILL`이 있다".
  - `domain/portfolio/_compiler.py:249,259` — tape hash payload의 `execution_timing`과
    `TargetTape.execution_timing`이 `spec.execution.timing`을 읽는다. `compile_target_tape`·
    `compile_target_tape_with_trace`가 `environment`(또는 `timing`)를 인자로 받게 하고
    `domain.portfolio` facade `DEPENDS_ON`에 `domain.backtest`를 추가한다(`domain.backtest`는
    `domain.portfolio`를 읽지 않으므로 순환이 아니다). `ExecutionTiming` 값이 하나뿐이라 지금은
    tape_hash가 변하지 않는다 — 회귀 테스트로 그 사실을 고정한다.
- **결정 항목 2개**(코드 변경 전에 PLAN 변경 기록에 결론을 남긴다).
  - `dataclass_json_schema`의 최종 owner. P2-01이 `domain/strategy/facade/schema.py`에서 수출하고
    `domain/backtest/_schema.py`가 읽는다. 전략과 무관한 표기법이라 `domain.backtest →
    domain.strategy` 화살표가 "유틸을 빌린다" 사유로 남는다 — enum을 옮겨 이 화살표를 끊으려 할 때
    빌더 때문에 남는다. 규칙 위반은 아니다(`DEPENDS_ON` 선언됨, facade가 책임 이름을 가짐).
  - 실행 설정 수치 범위(`RunEnvironment.__post_init__`과 런타임 스키마가 읽는 행)의 최종 owner.
    P2-01은 `_constraints.py`의 `/execution/*` 행을 필드 이름으로 다시 걸어 쓴다. 이 PR이
    `execution` 섹션을 지우면 그 행들이 전략 문서 포인터를 잃으므로 `domain/backtest`로 옮긴다.
  - 실행 설정 스키마를 `application/strategy_authoring`이 서빙하는 것(P2-01 acceptance가 지정)도
    같이 본다. authoring 유스케이스가 실행 설정을 소유하지는 않는다. 옮긴다면 P3-02와 함께.
  - **`preflight`가 `environment`를 받지만 읽지 않는다.** P2-01의 `start()`는 브리지가 validator
    보다 먼저 터지지 않도록 **해소 전** `spec.environment`를 넘기고, `_run`은 해소된 값을 넘긴다.
    지금은 `preflight`가 문서만 검사해 무해하지만, P2-01의 AST 가드는 "키워드가 있는가"만 보므로
    두 호출부가 **서로 다른 값**을 넘기는 상태를 통과시킨다. `_prepare`가 실행 설정을 읽게 되는
    순간 `start()`는 문서 값으로, `_run`은 명시값으로 판정해 P2-01 P0과 같은 모양이 된다. 가드를
    "두 호출부가 같은 값을 넘긴다"로 강화할지, `preflight` 시그니처에서 `environment`를 빼
    문서 전용임을 타입으로 못 박을지 이 PR에서 정한다.
- runtime schema fixture 재생성. OpenAPI 재생성과 생성 SDK 재생성(1절 규칙).

**제약사항**: P2-09 전까지 1.1 row는 읽을 수 없다(테스트 DB만). frontend 소비자 배선은 P3-01까지
그대로다(생성 SDK는 1절 규칙대로 각 PR이 재생성한다).
이 PR이 P2 스택에서 12절 상한(600줄·10파일)에 가장 가깝다. 착수 시 바뀌는 파일 수를 먼저 세고,
상한을 넘으면 **enum 이동을 별도 PR로 뗀다**(`data`·`execution` 제거가 먼저, enum 이동이 뒤). 분리하면 PR
총수가 바뀌므로 README의 "WORKFLOW의 PR 범위를 바꾸면 먼저 변경 이유를 PLAN.md 변경 기록에 남긴다" 절차를
따르고 집계 도구를 다시 돌린다.
그래도 넘으면 PR 본문 `제약사항`에 사유를 적는다.

### P2-04 — `signal.normalization`과 결합 전 정규화

**Intent**: spec D3 S4·D4. 아이디어 2(저PBR+고ROE 결합)를 언어가 표현하게 한다.

**Acceptance**

- `signal.normalization: none|rank|zscore`(새 문서 기본 `rank`). 컴파일러
  (`domain/portfolio/_compiler.py:526-570`)가 결합 전에 적용.
- `none`이 1.1과 수치 동일한 회귀 테스트. `rank`·`zscore` 수치 테스트.
- `_explanation.py`·semantic diff·contract 설명 갱신. i18n 키 추가는 P3-01.
- runtime schema fixture 재생성, `export_openapi.py`로 `backend/openapi.json` 재생성(`normalization`
  enum이 응답 스키마에 노출된다)과 생성 SDK 재생성(1절 규칙).

### P2-05 — 횡단면 eligibility(`EligibilityOperator`, exhaustive `_compare`, 2-pass)

**Intent**: spec D3 S5. 아이디어 4(거래대금 상위 20%).

**Acceptance**

- `EligibilityRule.operator`를 **전용 `EligibilityOperator`**(`gt`·`gte`·`lt`·`lte`·`eq`·
  `top_percent`·`top_count`)로 분리한다. 공유 `ComparisonOperator`에 얹지 않는다.
- `_compiler.py:1008-1017`의 `_compare`를 **exhaustive**로 바꾼다. catch-all
  `return value == threshold`를 제거하고 미지 연산자는 raise. 회귀 테스트로 고정.
- 프레임 컴파일 2-pass: 1-pass 절대 규칙(`gt`~`eq`), 2-pass 횡단면 `top_*`. AND 결합.
- 모집단은 유니버스 멤버 중 절대 규칙 통과 + 해당 `field_id` 값이 있는 종목. 결측은 탈락이고
  분모에서도 뺀다. 동점은 값 내림차순 → `security_id` 오름차순 cut(결정성 테스트).
- `top_percent` 0.2가 후보 100개에서 정확히 20개를 남긴다는 수치 테스트, 경계(소수점 절사)·동점·
  결측 케이스 각 1개.
- **탈락 사유**: `domain/portfolio/_models.py`의 `ExclusionReason`에 횡단면 cut 전용 값을 추가한다
  (현재 12종에 순위 탈락에 해당하는 값이 없어 `ELIGIBILITY_FAILED`로 표시되면 "규칙 위반"으로
  잘못 읽힌다). 2-pass 구조상 `_score_candidate`(`:454-459`)는 1-pass 사유만 내고, 2-pass가 cut된
  종목에 새 사유를 덧붙인다.
- trace 투영과 `application/portfolio_design/_trace_service.py`가 새 사유를 그대로 전달한다. P4-03
  미리보기 패널의 "유니버스·필터 통과·결측 제외 수"가 규칙 탈락과 순위 탈락을 구분해 셀 수 있어야
  한다.
- `ExclusionReason`은 `backend/openapi.json`의 응답 스키마에 노출되어 있으므로(현재 4곳) 멤버 추가는
  **API 계약 변경**이다. `export_openapi.py`로 재생성하고 diff를 확인한다(2절 "API contract" gate).
  생성 SDK도 같은 PR에서 재생성한다(1절 규칙). 재생성을 빠뜨리면 프론트가 모르는 enum 값을 받아
  trace 화면이 빈칸을 낸다.

### P2-06 — `risk.risk_factor_id`, `saved_factor`·`saved_subgraph` 제거

**Intent**: spec D3 S6~S7. 아이디어 5(변동성 역가중).

**Acceptance**

- `risk.risk_factor_id`(nullable, `x-reference: factor`). `risk_field_id`와 동시 지정은
  `strategy.risk.risk_source_conflict` **error**다. 이 배타 규칙은 적용 조건이 아니라 별개 validator
  검사이므로 `FIELD_APPLICABILITY`에 넣지 않는다.
- `FIELD_APPLICABILITY`에 `/risk/risk_factor_id` 행을 추가한다. 조건은
  `/portfolio/weighting = risk` **하나뿐이고 `owned_by_error`를 붙이지 않는다**
  (`_constraints.py:193-197`의 `/risk/risk_field_id` 행과 같은 모양). 붙이면 validator가 그 error
  하나만 내고(`:138-140`) `strategy.field.inapplicable` warning이 억제되어, `weighting: equal`에서
  `risk_factor_id`를 남겨 둬도 아무 경고가 없게 된다. 다른 `weighting`에서 설정하면 warning이 나고
  **합성 제외도 일어나지 않는다**. 적용 조건이 붙은 필드가 조건 밖에서 알파 합성을 바꾸면
  "모드별로 읽히는 필드" 계약과 어긋난다.
- `weighting: risk` + `risk_factor_id`일 때 참조 팩터는 **합성 점수에서 제외**한다(`weight` 무시,
  분모 `Σ|weight|`에서도 제외). 제외 사실은 `strategy.risk.risk_factor_excluded` warning. 역가중은
  `signal.normalization` **이전** 원시 출력(`PortfolioObservation.factor_values`)을 쓴다. 값이
  `<= 0`이면 기존 `MISSING_RISK` 탈락 유지(`_compiler.py:841-843`).
- **제외 후 남는 알파 팩터가 0개면 compile error `strategy.signal.no_alpha_factor`**(코드 레지스트리
  등록). 막지 않으면 `denominator == 0` → `composite_score` 전부 `None` → 정렬 키 `or 0.0` 폴백
  (`_compiler.py:345`, `:400`, `:835`)으로 **`security_id` 사전순 상위 N이 조용히 선정된다.** 테스트:
  팩터 1개짜리 문서에서 그 팩터를 `risk_factor_id`로 지정하면 compile error 1건.
- 수치 테스트(기준선을 명시한다): **알파 팩터 N개짜리 문서**와, **거기에 변동성 팩터 1개 +
  `risk_factor_id`를 함께 추가한 문서**의 선정 종목이 같고 비중만 다르다. "붙이기 전후 비교"가
  아니다 — 붙이기 전 문서에는 그 팩터가 합성에 들어 있으므로 선정이 바뀌는 것이 정상이다.
  `normalization: rank`에서도 역가중이 원시값 기준이다.
- `saved_factor`·`saved_subgraph`를 노드 union·스키마·연산자 카탈로그에서 제거. 실행 경로의 거부
  코드 삭제.
- runtime schema fixture 재생성, `export_openapi.py`로 `backend/openapi.json` 재생성(`risk_factor_id`
  필드가 응답 스키마에 노출된다. 진단 코드 문자열은 OpenAPI에 열거되지 않는다)과 생성 SDK
  재생성(1절 규칙).
- BACKLOG-001: `price.momentum_12_1` 시드의 `history=252`가 그래프 최소 이력 273과 다르다
  (`window=252` + `lag=21`). 시드 값을 그래프에서 파생하거나 둘이 같은지 단언하는 테스트를 둔다.

### P2-07 — compile 단일 게이트: `field_missing`, boolean 승격, 단위 경고, 연산자 unsupported

**Intent**: spec D5. "검증 통과 = 실행 가능".

**Acceptance**

- compile 서비스가 연결된 equity 어댑터의 필드 메타데이터를 `validate_strategy`에 넘긴다.
  `field_missing`이 compile에서 난다. 어댑터가 없으면 지금과 같다.
- 팩터 출력이 boolean이면 canonical 그래프 끝에 0/1 승격 노드가 붙는다(hydrate 단계, 사용자 문서
  불변, node_id `__promote_<factor>`). scalar 출력은 `strategy.factor.output_type` error(compile).
  `_reject_non_numeric_factor_outputs`가 compile 통과 문서에서 절대 발화하지 않는 property 테스트.
- `signal.normalization: none`이고 팩터 단위가 다르면 `strategy.signal.unit_mismatch` warning.
- 어댑터 capability(`GROUP_SERIES` 제공 여부)를 compile이 조회해 `group` 노드가
  `strategy.operator.unsupported` error. 연산자 카탈로그 응답의 `availability`도 같은 capability로.
- `export_openapi.py`로 `backend/openapi.json` 재생성(연산자 카탈로그 응답의 `availability` 값 집합이
  바뀐다. 진단 코드 문자열은 OpenAPI에 열거되지 않으므로 그 자체는 재생성 사유가 아니다. diff가 0이면
  그 사실을 PR 본문에 적는다). diff가 있으면 생성 SDK도 재생성한다(1절 규칙).
- **backlog(P2-05 리뷰 DEFECT-P3-1)**: 횡단면 eligibility 규칙이 모집단을 0으로 만드는 쪽에는
  진단이 없다. `top_percent: 0.001`에 모집단 100이면 cut이 0이라 전원 `ELIGIBILITY_RANK_CUT`이다.
  P2-05의 `strategy.eligibility.rule_value`는 "너무 관대한" 쪽(`20`을 비율로 적는 실수)만 막는다.
  결과가 빈 포트폴리오라 조용하지 않아 P3으로 뒀다 — 이 PR에서 frame warning으로 다룰지 정하고,
  다루지 않으면 그 판단을 PLAN 변경 기록에 남긴다.
- BACKLOG-003: 횡단면 `zscore`·`rank`의 `unit_rule`을 무차원(`"1"`)으로 바꾼다. 단위가 다른 두 필드를
  표준화해 더한 그래프가 `factor.graph.unit_mismatch` 없이 통과하는 재현 그래프 테스트와, `demean`·
  `winsorize`는 입력 단위를 보존하는 대조 테스트를 `test_factor_operators.py`에 둔다.

### P2-08 — duckdb `GROUP_SERIES` 스파이크와 `ideas/*.yaml` 5개

**Acceptance**

- duckdb 어댑터가 `GROUP_SERIES`(섹터 코드 필드 1개)를 제공하는 최소 구현을 시도한다
  (`equity_duckdb/_adapter.py:734-747`이 현재 모든 필드를 `NUMERIC_SERIES`로 고정 반환). 범위를
  넘으면 **PLAN 변경 기록에 사유를 남기고 `unsupported`로 둔다**(P2-07 분기가 그대로 남는다).
- fixture `ideas/*.yaml` 5개(spec 5절)가 hydrate·validate·preview까지 통과(backend 수준 완료 정의).
- **ideas fixture는 레시피 빌더 산출 형태를 따른다**(spec D2). 다중 입력 연산자의 부가 입력은 새
  소스 잎 노드다. 아이디어 3은 노드 4개(그중 잎 2개: `close`·`close_2`)다 — `close` → `ma20` → 잎
  `close_2` → `breakout(gt, left=close_2, right=ma20)`. `comparison` 출력이 P2-07의 승격으로
  통과한다. 체인 머리를 재참조하는 노드 3개 형태로 쓰지 않는다 — P5-03의 "e2e 산출물과 같은 hash"
  단언이 깨진다.
- 아이디어 5(변동성 역가중)는 **알파 팩터 1개 + 변동성 팩터 1개 두 벌**로 쓰고 `risk_factor_id`가
  변동성 팩터를 가리킨다. 변동성 팩터 하나만 두고 그것을 참조하면 P2-06의
  `strategy.signal.no_alpha_factor`에 걸려 compile이 막힌다.

### P2-09 — 1.1 → 1.2 업그레이더(버전 디스패치), upgrade 응답 `environment`, 동결 읽기, OpenAPI

**Intent**: spec D7. 1.1 이력이 동결되고 업그레이드 경로가 열린다.

**Acceptance**

- `_upgrade.py`를 **버전 디스패치**로 다시 쓴다.
  `UPGRADE_STEPS: Mapping[str, tuple[UpgradeStep, ...]]`(키는 from-version `"1.0"`·`"1.1"`), 공개
  API는 `upgrade_document(tree) -> UpgradeOutcome(tree, environment, warnings)` 하나. 문서의
  `schema_version`에서 `CURRENT_SCHEMA_VERSION`까지 체인을 순서대로 적용하고 **각 중간 단계마다**
  그 버전을 찍고 검증한다. 현재 `_step_schema_version`(`:58-59`)이 곧장 최신 버전을 찍는 동작을
  고친다.
- 심볼 교체와 호출자 갱신을 같은 PR에서: `LEGACY_SCHEMA_VERSION` → `FROZEN_SCHEMA_VERSIONS`,
  `is_legacy_document` → `is_upgradeable_document`, `upgrade_document_1_0` → `upgrade_document`,
  `NotALegacyDocumentError` → `NotUpgradeableDocumentError`. facade
  `domain/strategy/facade/document.py`의 공개 심볼, 호출자 3곳
  (`adapters/outbound/document_codec/_upgrade_source.py`,
  `adapters/outbound/strategy_sqlite/_record_codec.py`,
  `application/strategy_authoring/_service.py`). `is_frozen_schema_version`은 **변경 없음**
  (테스트로 고정).
- P2-03이 저장 row 읽기를 위해 앞당긴 세 심볼도 같이 흡수한다:
  `RETIRED_SCHEMA_VERSIONS`(현재 `{"1.0", "1.1"}` 리터럴 집합) → `FROZEN_SCHEMA_VERSIONS =
  frozenset(UPGRADE_STEPS)`(체인 키에서 유도해 버전 추가 시 한 곳만 고친다),
  `require_retired_schema_version` → 새 술어 이름으로 개명, `UnknownSchemaVersionError` →
  `NotUpgradeableDocumentError`로 합치거나 그 계열 이름으로. 호출자는
  `adapters/outbound/strategy_sqlite/_record_codec.py` 하나이고, 미지 버전 fail-closed 회귀
  테스트(`tests/contract/test_strategy_repository_retired_1_1.py`)를 그대로 통과시켜야 한다.
- 1.1 → 1.2 step(spec D7 변환 목록): `data`·`execution`·`graph.missing_policy` 제거 후 `environment`로
  반환(팩터별 정책이 다르면 첫 값 + warning), `signal.normalization: none` 명시, `saved_*` 노드는
  `strategy_document.upgrade_unsupported_node` 422. dict 경로·source 경로(ruamel) 같은 step 맵,
  drift fail-closed.
- `weighting: factor_score` 문서는 업그레이드 뒤 목표 비중이 1.1 결과와 다를 수 있다(P2-04
  결정 5). 선정·보유 종목은 같고, 비중이 1.1 과 같은 경우는 롱이면서 강도 기준점이 정확히 0 일
  때뿐이다. `direction: low`·`long_short`·부호 섞인 점수·일반 양수 점수 모두 달라질 수 있다.
  "1.1 결과 보존" 검증에서 이 차이를 회귀로 세지 않도록 기대값을 결정 5 규칙으로 계산한다.
- `POST /strategy-documents/upgrade` 응답에 `environment`·`warnings`.
- repository codec이 1.0·1.1 row를 업그레이드해 읽고 무결성 검증 3종(1.1 spec D2 방식).
  saved-reference backtest 422.
- golden 파일 역할(spec D7): `quality_momentum.v1_2.commented.yaml` 신규 = 1.0 문서의 **최종** 기대
  출력. `tests/application/test_strategy_authoring_upgrade.py:69`,
  `tests/contract/test_document_upgrade_source.py:50`,
  `tests/integration/test_strategy_document_upgrade_http_api.py:33`의 기대 파일이 이것으로 바뀐다.
  `.v1_1.commented.yaml`은 1.0 → 1.1 **중간 단계** 고정용으로 남긴다(체인 중간 검증 테스트).
  `.v1_1.yaml`(P2-03 보존분)이 1.1 → 1.2 입력이다. 세 경로 모두 주석·순서 보존, dict 경로와 tree 동일.
- 업그레이드된 문서의 preview 결과가 1.1 결과와 같다(`normalization: none` 보존).
- SoT 업그레이드 행의 "(1.2 step은 P2-09에서 추가, 아직 없음)" 예약 표기를 해제하고, 금지 절의
  "frontend에 1.0 → 1.1 업그레이드 규칙과 …를 복제하지 않는다" 문장을 "1.0 → 1.1 → 1.2"로 고친다
  (금지 절의 다른 문장 "JSON/Form/Graph/Diff projection"은 P4-04 몫이다).
- `is_upgradeable_document`에 **버전 상한**을 둔다. P1-05가 그 판정을 "버전이 은퇴 버전이거나
  본문이 옛 판 모양"으로 넓혔는데(진단이 "업그레이드하세요"라고 시킨 문서를 endpoint가 거절하던
  모순을 없애려고), 아는 버전이 1.0·1.1 둘뿐이라 상한이 없어도 됐다. 1.2가 들어오면 "미래 버전 +
  옛 키 하나"가 1.1로 강등되는 경로가 되므로, 버전 디스패치를 넣는 이 PR에서 판정에 상한을 함께
  둔다(P1-05 2차 리뷰 P3-7).
- **BACKLOG-010(P2 구현자 관찰)**: 위 상한을 넣으면서 `is_upgradeable_document` docstring 도 고친다.
  지금 docstring 은 "지금은 아는 버전이 1.0·1.1 둘뿐 … schema 1.2 가 들어오면"이라고 적는데, 1.2 는
  P2-03 부터 현재 버전이라 이 문장은 이미 사실이 아니고 상한 추가를 이 PR 에 떠넘기는 문장만
  남았다.
- **BACKLOG-011(P2 구현자 관찰)**: `structure.invalid_date` 를 정리한다. 1.2 문서에는 날짜 필드가
  없어(`data.start`·`end` 가 실행 설정으로 이동, P2-03) 문서로는 도달할 수 없는 코드다. 지금은
  `STRUCTURE_CODES` 에 남아 golden 테스트가 날짜 필드 하나짜리 가짜 모델로 문장을 고정한다. 업그레이더
  가 1.0·1.1 원문의 날짜를 읽는 경로에서 쓰이지 않으면 코드·분기·golden 을 함께 지운다.
- OpenAPI 재생성. `database/tests` 계약 확인.

**Phase 2 exit**

- [ ] 1.2 fixture 같은 hash. 1.1 fixture 전부 업그레이드 통과.
- [ ] compile 통과 문서가 preview에서 422 없음(property).
- [ ] `ideas/*.yaml` 5개 backend 통과.
- [ ] `plan_hash`가 결측 정책으로 계속 갈린다(P2-02 회귀 테스트).
- [ ] SoT·책임분리 점검 서브에이전트 blocking 0. SoT 행 "실행 설정" 채움(owner 구현 완료), 업그레이드 행 예약 표기 해제.

---

## 6. Phase 3 — frontend 1.2 적응

### P3-01 — SDK 1.2, pointer 헬퍼·outline·snippet·Form projection·plan·debugger 적응

**Acceptance**

- `npm run api:generate` 후 diff 0. `/data/*`·`/execution/*`·`/graph/missing_policy` pointer 참조가
  소스·테스트에서 사라진다(grep 0건 테스트).
- `signal.normalization`·횡단면 eligibility·`risk_factor_id`의 i18n(설명·적용 조건) 추가. Form이 새
  필드를 스키마에서 자동으로 그린다(손으로 적지 않는다). `risk_factor_id` 의 이름·설명·적용 조건 키는
  P2-06 이 먼저 넣었다(runtime schema 가 발행한 키는 전부 번역돼야 한다는 단위 테스트가 강제한다).
- **BACKLOG-012(P2-06 관찰)**: `saved_*` 제거로 도달할 수 없게 된 frontend 잔재를 지운다. Contract
  Inspector 의 팩터 카탈로그 join(`contract-inspector.ts` 의 `catalog === "factor"` 분기와 그 자원
  로딩), `form-projection.ts` 의 `CATALOGS` 중 `factor`·`subgraph`, `schema-assist.ts` 의 `subgraph`·
  `factor` 카탈로그 분기, i18n 키 `strategy.node.saved_*`·`strategy.field.node.factor_id`·
  `strategy.field.node.subgraph_id`·`assist.catalog.subgraph`.
- outline·snippet 카탈로그(팩터 preset은 "예시" 그룹으로 강등, 튜토리얼 전용)·execution plan·graph·
  debugger가 1.2 pointer로.
- 단위 테스트 전부 green. e2e fixture는 P3-03.

### P3-02 — 실행 설정 패널 확장, 1.1 업그레이드 배너, 실행 설정 띠

**Acceptance**

- 실행 설정 패널(`backtest-run-settings.tsx`)에 시장·빈도·기간·유니버스·체결·수수료·참여율·슬리피지·
  결측 처리. 기본값은 **실행 설정 스키마** `GET /api/v1/run-environments/schema`(P2-01)에서 온다.
  전략 authoring runtime schema와 다른 산출물이며, 프론트에 기본값을 손으로 적지 않는다(테스트로
  고정). 마지막 사용값은 전략별 `localStorage`. 유니버스는 기존 catalog picker.
- **범위(`minimum`/`maximum`)의 SoT는 `/run-environments/schema`다.** `backend/openapi.json`의
  `RunEnvironment`에는 범위가 없다 — pydantic이 `__post_init__`를 들여다보지 못해 생성 SDK 타입에
  실리지 않는다(P2-01에서 OpenAPI 재생성 diff가 0인 이유이기도 하다). 생성 타입만 믿는 화면은
  서버가 거부할 값을 유효한 것으로 보므로, 패널은 범위도 스키마 엔드포인트에서 읽는다(테스트로 고정).
- **BACKLOG-013(P2-01·P2-02 병합 리뷰 P3)**: 실행 설정 스키마가 발행하는 설명 키
  `strategy.field.run_environment.*` 7개(`start`·`end`·`market`·`frequency`·`universe_id`·`timing`·
  `missing`)에 frontend 문장(한국어·영어, 이름과 `.description`)을 붙이고, `/run-environments/schema`
  가 발행한 설명 키가 전부 번역됐는지 보는 커버리지 테스트를 둔다(전략 runtime schema 쪽
  `screen-vocabulary.test.ts` 와 같은 모양). 패널 항목 옆 한 줄 뜻 표시는 US-SM-10 과 잇는다.
- **결정 항목**: 명시 `environment`의 422를 필드 단위로 어떻게 표면화할지. 같은 사실이 문서에
  있으면 `strategy.execution.participation` 코드가, 실행 설정에 있으면 pydantic 기본 분기가 나간다
  (`Backtest422Response`가 `RequestValidationResponse`를 이미 union에 가져 계약 위반은 아니다).
  `loc`이 `["body","environment"]`까지만 가리켜 어느 필드인지 구조화된 형태로는 알 수 없고 메시지
  문자열에만 있다. 패널이 필드 옆에 오류를 붙이려면 파싱해야 하므로, inbound 계층에서
  `RunEnvironment`를 먼저 구성해 코드화된 detail로 바꿀지 결정한다.
- 기간이 전략 문서에서 오던 `dateRange` 의존 제거. OOS 창 검증은 실행 설정의 기간으로.
- **P2-03이 잠근 `test.fixme` 4건을 해제한다**: `workbench.workflow.spec.ts`의
  `creates, recovers, validates, versions, traces and backtests`와
  `upgrades a frozen 1.0 revision …`, `workbench.real-equity.spec.ts`의
  `edits the graph on real data …`, `workbench.infrastructure.spec.ts`의
  `keeps a real debugger trace legible and inside the viewport`(`strategy-debugger.png` 기준선
  4장도 그때 화면으로 재생성). 패널이 생기기 전에는 프론트가 `environment`를 싣지 못해
  브라우저에서 시작한 run이 422 `backtest.run.environment_required`로 거절된다.
- **전략 디버거 trace 요청도 같은 배선이 필요하다.** `POST /api/v1/strategies/debug/trace`가
  `environment` 없이 나가면 preview·run과 같은 `portfolio.strategy.invalid` +
  `run_environment.required`로 거절되어 "추적 재현 정보" 패널이 뜨지 않는다. 위 fixme
  시나리오 안에 있으므로 해제와 같이 고친다. 최종 시나리오 재작성은 P3-03이 맡는다.
- **디버거 컨텍스트의 `start`/`end`를 다시 non-null로 만든다.** P2-03이 실행 기간의 출처를
  잃어 `widgets/strategy-ide/model/strategy-debugger-context.ts`가 두 값을 `null`로 고정했고,
  그 결과 `features/debug-strategy/model/strategy-trace.ts`의 응답 날짜 범위 가드(`as_of`가
  실행 기간 안인가, `execution_on <= end`인가)와 날짜 입력의 `min`/`max`가 꺼져 있다. 패널이
  기간을 갖게 되면 두 필드를 실행 설정에서 채우고 타입을 `string`으로 되돌린다 — `string |
  null`인 채로 끝나면 가드가 영구히 꺼진 채 남는다(P2-03 리뷰 P3-06).
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
- BACKLOG-002: 매뉴얼 스크린샷 14장을 `npm run docs:capture`로 1.2 한글 화면으로 다시 찍고, 8절에
  "초안 복구·서버 초안 적용 직후 되돌리기는 복구 이전 텍스트로 돌아간다"는 안내를 넣는다.
- BACKLOG-008: 충돌 표식 게이트(`tools/quant_study_dev/conflict_markers.py`)가 `git ls-files`로 추적
  파일만 열거하고, `.lock`과 비 UTF-8 텍스트도 검사하며, 주석이 7자 밑줄 검출을 사실대로 적는다.
  cp949·UTF-16·`.lock`·`build/`·`dist/` 표식 검출 테스트를 둔다.
- BACKLOG-009: 사이드바 제안을 "문서에 적용"한 뒤 툴바 "실행 취소" 한 번으로 적용 전 원문으로 돌아가는
  브라우저 스토리 e2e(`@story`·`@US-DM-09`)를 더하고 US-DM-09를 `구현됨-e2e`로 올린다.

**Phase 3 exit**

- [ ] CI 전체 green.
- [ ] 같은 전략·다른 기간 → 같은 spec_hash e2e.
- [ ] SoT·책임분리 점검 blocking 0.

---

## 7. Phase 4 — 그래프 1수준: 파이프라인

### P4-01 — `pipeline-projection.ts`

**Acceptance**

- runtime schema × parse tree × compile 진단 → 표준 단계 모델(1 유니버스 `eligibility`, 2 알파 팩터
  `factors`+`signal`, 3 포트폴리오 구성 `portfolio`, 4 리스크 제약 `risk`; 5 실행은 실행 설정 띠).
  YAML 섹션과 1:1. `weighting: risk`의 기준 팩터(`risk.risk_factor_id`)는 3단계 비중 카드가
  편집한다(카드가 pointer 여러 개를 가질 수 있다, `x-stage`). 전략 한 문장 요약(`strategySummary`)을 같은 투영이 i18n 문장 틀로
  만든다(예: "거래대금이 10억 원 이상인 종목 중에서, 모멘텀 60%·가치 40%로 합친 순위 점수가 높은
  20종목을 매월 골라 같은 비중으로 보유한다"). 각 카드는 pointer, 한글 라벨(i18n 키), 현재 값(작성 여부), 컨트롤
  종류(`form-projection`의 필드 행 재사용), 진단 목록. 필드의 단계 배정은 스키마 `x-stage` 마커로
  backend가 준다(프론트에 필드 목록을 손으로 적지 않는다; backend 변경은 이 PR에 포함, 1.2 계약
  additive).
- 팩터 카드 요약 문장: **이 PR이 `features/edit-strategy/model/recipe-projection.ts`를 만들고
  P5-01이 같은 파일을 확장한다**(별도 헬퍼를 만들지 않는다). 여기서는 spec D2의 체인 판정만
  구현한다. 체인이면 "종가 → 252일 모멘텀(21일 제외) → 순위"(연산자 카탈로그 i18n 이름 +
  파라미터), 아니면 "노드 N개 · 고급".
- 스키마 fixture만으로 유도되는 단위 테스트.

### P4-02 — 단계 카드 UI(유니버스·포트폴리오 구성·리스크 제약)와 실행 설정 띠

**Acceptance**

- 문구 규칙(spec D9): 단계 이름은 표준 용어(한글 + 영문 소제목) + 한 줄 쉬운 설명, 컨트롤은 한국어 문장 안에("거래대금이 [10]억 원 [이상인]
  종목만", "합산 점수 상위 [20]종목을 [매월] 다시 고른다"), 용어 대신 결과로 설명. 캔버스 맨 위에
  한 문장 요약 띠. 문장 틀은 i18n, 값은 parse tree.

- `pipeline-panel.tsx`: 4열 캔버스. eligibility 규칙 추가·삭제(`insertItem`·`remove`), 조건 컨트롤
  (이상·이하·상위 %·상위 N개 → `operator`·`value`), 정규화 선택(`signal.normalization`) 카드에 단위
  경고 문장 인라인, 선택·리밸런스·비중·한도 필드는 Form 컨트롤 재사용. 적용 조건 경고는 카드
  안에서 필드를 흐리게.
- 실행 설정 띠(P3-02)가 캔버스 위. "여기 없는 것" 안내 카드.
- 키보드 조작·ARIA(`frontend-ui-quality.md`).

### P4-03 — 알파 팩터 단계(팩터 카드·점수 합치기), 빈 팩터 추가, 기준일 미리보기 패널

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
- SoT 갱신: 금지 절의 "YAML/JSON source ↔ StrategySpec ↔ JSON/Form/Graph/Diff projection" 문장에서
  은퇴한 Form·JSON 투영을 빼고 그래프 표현 세 수준으로 다시 쓴다. DSL 문장은 유지. 이 문장을
  갱신하는 PR은 이 PR 하나다(P0-01 리뷰 P3 finding).
- BACKLOG-005: 좁은 폭 지원 하한을 정해 기록하고, 그 폭에서 뷰 탭 두 개가 보이고 눌리는 단언을 둔다
  (지금은 640px 이하에서 탭 줄 폭이 0).
- BACKLOG-006: Form 탭을 걷어도 문제 행 클릭이 편집 가능한 가장 깊은 요소로 스크롤하는 route
  테스트("scrolls to the editor row, not the plan node")를 새 탭 구성 기준으로 유지한다. 지우지 않는다.
- BACKLOG-007: JSON 탭 은퇴로 `document-routes.test.tsx` "professional keyboard workflow (P6-03)"의
  read-only view 경로 테스트를 다시 쓸 때, 부하 중 한 틱 늦는 reveal을 기다리는 방식으로 flake를 없앤다.

**Phase 4 exit**

- [ ] 빈 화면 e2e green.
- [ ] 파이프라인 수준 식별자 0개 단언 green.
- [ ] SoT·책임분리 점검 blocking 0.

---

## 8. Phase 5 — 그래프 2수준: 레시피

### P5-01 — `recipe-projection.ts`·`recipe-transactions.ts`

**Acceptance**

- 체인 판정(정본은 spec D2)을 P4-01이 만든 `recipe-projection.ts`에서 **확장**한다. 새 파일을
  만들지 않는다. 규칙: 소스 노드 하나로 시작해 각 노드가 직전 노드만 참조하고 `output_node_id`가
  마지막이면 체인. 다중 입력 노드는 한 입력이 체인 꼬리이고 나머지 입력이 전부 체인 밖 잎
  (`field`·`constant`·`parameter`)일 때만 체인("÷ 시가총액", "종가 > 20일 이평"). **체인 머리
  재참조는 비체인**이다. 그 외는 "고급에서 편집".
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
  결과 원문이 `fixtures/strategy_documents/ideas/*.yaml`(P2-08)과 같은 hash. 두 수준 DOM 식별자
  0개 단언. fixture와 빌더 산출물이 같은 형태라는 근거는 spec D2의 "다중 입력 연산자의 부가 입력은
  항상 새 소스 잎 노드다"이며, 아이디어 3은 양쪽 모두 노드 4개(잎 2개) 형태다.
- 매뉴얼에 그래프 화면 절과 예시 5개(튜토리얼).
- BACKLOG-004: 미리보기가 `POST /api/v1/factors/preview`를 쓰면 그 422(`factor.graph.invalid`와
  `factor.graph.*` 이슈 코드)를 `strategy.expression.*` 네임스페이스로 정리한다. 이 endpoint를 쓰지
  않으면 이 PR에서 BACKLOG-004의 담당을 다시 정한다.

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
- BACKLOG-006(후속): 목록형 편집기를 걷어도 문제 행 reveal이 캔버스의 편집 가능한 노드로 가는
  테스트를 유지한다.

**Phase 6 exit**

- [ ] 드래그 배선 → YAML 반영 → 되돌리기 e2e.
- [ ] SoT·책임분리 점검 blocking 0. initiative 완료 정의 6항 전부 PLAN에 기록.
