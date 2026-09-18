# schema 1.1 · Form/Graph 편집 구현 워크플로우

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` 또는
> `superpowers:executing-plans`로 PR 단위로 실행한다. PR마다 [YAML Strategy Workbench WORKFLOW
> 13절](../strategy-workbench-yaml-ui/WORKFLOW.md) 절차(scope packet → self-check → diff freeze →
> Opus reviewer 1명 → 재검토 → APPROVE → merge)를 지킨다. 진행 상태는 [PLAN.md](./PLAN.md)만
>갱신한다.

**Goal:** 전략 YAML을 schema 1.1로 간소화하고, Form과 Graph에서 source 트랜잭션으로 전략을
편집할 수 있게 한다.

**Architecture:** backend가 1.1 모델·업그레이드 변환·적용 조건표를 한 벌 소유한다. frontend는
runtime schema와 parse tree로 Form/Graph를 그리고, 모든 GUI 동작을 JSON Pointer 범위의 YAML
텍스트 교체(`replaceRange` 단일 undo 트랜잭션)로 번역한다. spec·hash·검증은 계속 backend compile만
믿는다. 계약은 [설계 spec](../../superpowers/specs/2026-09-17-strategy-schema-1-1-and-gui-editing-design.md)이
소유한다.

**Tech Stack:** Python 3.11 dataclasses · ruamel.yaml(safe + round-trip) · FastAPI · SQLite ·
React 19 · TanStack Router/Query · CodeMirror 6 · `yaml` 2.9 · Vitest · MSW · Playwright

---

## 1. 실행 순서와 스택

```text
main
 └─ docs/strategy-gui-editing-plan           (이 패키지 + 설계 spec)
     └─ P1-01 ─ P1-02 ─ P1-03 ─ P1-04 ─ P1-05   backend schema 1.1
         └─ P2-01 ─ P2-02 ─ P2-03              frontend 1.1
             └─ P3-01 ─ P3-02                  source 트랜잭션
                 └─ P4-01 ─ P4-02 ─ P4-03 ─ P4-04   Form 편집
                     └─ P5-01 ─ P5-02 ─ P5-03       Graph 편집
```

- 브랜치 이름은 `feat/gui-<pr-id 소문자>-<slug>`. 예: `feat/gui-p1-01-schema-1-1-model`.
- 각 PR의 base는 직전 PR 브랜치다. APPROVE 후 로컬 main에 `--no-ff` merge하고 다음 PR을 잇는다.
- **generated SDK 규칙(중요)**: P1 backend PR은 `backend/openapi.json`만 재생성하고
  `frontend/src/shared/api/generated`는 건드리지 않는다. frontend 코드가 1.0 pointer를 쓰는 동안
  SDK를 바꾸면 frontend typecheck가 깨져 stack의 모든 PR gate가 실패한다. SDK 재생성과 frontend
  적응은 P2-01이 한 PR에서 한다. 각 P1 PR 본문 `제약사항`에 이 사실을 적는다.
- 실 DB 주의: P1 merge 전에는 실 SQLite에 1.1 revision을 저장하지 않는다(spec 6절 롤백).
- **CI `frontend`·`browser-e2e` job은 P1 PR에서 빨간불이다(알려진 상태, P1-01부터)**: `frontend` job의
  `npm run api:generate` 뒤 `git diff --exit-code`(생성 SDK 동기), 공유 fixture(`runtime-schema.json`,
  `quality_momentum.*`)를 읽는 Vitest, 그리고 실제 backend를 띄우는 Playwright가 모두 1.1 backend에
  걸린다. SDK만 재생성하면 `spec.factors.factors`를 쓰는 소스 5곳과 테스트 25곳에서 typecheck가
  깨지므로 P1 PR은 backend gate(pytest·Ruff·Pyright·추적 openapi 동기 테스트)만 merge gate로 삼고,
  frontend job green은 P2-01(SDK·단위 테스트)과 P2-02(e2e)의 exit 조건이다. 각 P1 PR 본문
  `제약사항`과 PLAN CI 열에 이 사실을 적는다.

## 2. 공통 gate

| PR 유형 | self-check |
|---|---|
| backend | focused pytest → `uv run pytest` 전체 → `uv run ruff check src tests` → `uv run pyright` |
| API contract | backend 전체 → `uv run python scripts/export_openapi.py openapi.json` → diff 확인 (P2-01부터 `npm run api:generate` 포함) → frontend 전체 |
| frontend | focused vitest → `npm run typecheck` → `npm run lint` → `npm test` → `npm run build` |
| E2E 포함 | 위 + `npm run test:e2e` |

runtime schema fixture는 backend 모델이 바뀔 때마다 `uv run python tools/export_runtime_schema.py`로
재생성한다(`tests/domain/test_strategy_schema.py::test_runtime_schema_fixture_is_current`).

---

## 3. Phase 1 — backend schema 1.1

### P1-01 — 모델 1.1: `factors` 평탄화, 선택 보일러플레이트, `kind` 우선

**Intent**: spec D1의 S1·S3·S5를 도메인 모델과 hydrate·schema·validation·compiler에 반영하고
fixture와 hash golden을 1.1로 옮긴다.

**Acceptance**

- `SUPPORTED_SCHEMA_VERSIONS == ("1.1",)`. `schema_version: "1.0"` 문서는
  `structure.unsupported_schema_version`으로 거부된다.
- `factors`는 최상위 sequence다. `factors: {factors: [...]}`는 `structure.type_mismatch`다.
- 다음이 생략된 문서가 hydrate되고, 명시한 문서와 같은 `spec_hash`를 낸다: `description`,
  `eligibility`, `signal`, `portfolio`, `risk`, `execution`, `data.market`, 팩터 `label`, `weight`.
- 생략된 팩터 `label`은 `factor_id`와 같다.
- runtime schema에서 모든 노드 `$defs`의 첫 property가 `kind`다. `label`은 required가 아니고
  `x-default-from: factor_id`를 가진다. `factors`에 `x-defines`를 두지 않는다: 문서 안에서 팩터를
  참조하는 `x-reference` namespace가 없어 "defines ↔ references" 불변식을 깨기 때문이다(P2-01은
  항목의 `x-authoring-identity`로 컬렉션을 찾는다).
- fixture `quality_momentum.yaml`(verbose 1.1), `.json`, `.legacy.json`, `.minimal.yaml`(생략형)이
  같은 hash를 낸다. 기존 1.0 fixture는 `quality_momentum.v1_0.yaml`로 보존한다(P1-03 golden).
- hash 알고리즘 golden은 모델과 무관한 literal payload로 고정된다(아래 Step 6).
- backend 전체 테스트·Ruff·Pyright 통과. `openapi.json` 재생성.

**Contract·SoT 변경**: authoring 문법(spec D1). 도메인 `StrategySpec` 필드 `factors` 타입.

**유지할 동작**: hash 알고리즘, `spec_hash`/`source_hash` 이원 구조, unknown key fail-closed,
scalar constraint catalog, 검증 코드 registry.

**예상 파일**

- Modify: `backend/src/strategy_workbench/domain/strategy/_models.py`
- Modify: `backend/src/strategy_workbench/domain/strategy/_hydrate.py`
- Modify: `backend/src/strategy_workbench/domain/strategy/_schema.py`
- Modify: `backend/src/strategy_workbench/domain/strategy/_validation.py`
- Modify: `backend/src/strategy_workbench/domain/strategy/_explanation.py`, `_diff.py` (factors 경로)
- Modify: `backend/src/strategy_workbench/domain/strategy/facade/specification.py` (`FactorStep` export 제거)
- Modify: `backend/src/strategy_workbench/domain/portfolio/_compiler.py`,
  `application/portfolio_design/_service.py`, `_trace_service.py`, `application/strategy_design/_service.py`
- Modify: `backend/tests/fixtures/strategy_documents/*`, `backend/tests/contract/test_strategy_authoring_fixtures.py`,
  `backend/tests/domain/test_strategy_schema.py`, 그 외 `factors.factors`를 쓰는 backend 테스트
- Regenerate: `backend/tests/fixtures/strategy_documents/runtime-schema.json`, `backend/openapi.json`

**Steps**

- [ ] **Step 1: 실패하는 계약 테스트 작성** — `backend/tests/contract/test_strategy_authoring_fixtures.py`

```python
def test_minimal_document_hydrates_to_the_same_hash() -> None:
    """생략된 섹션·label·weight·market은 기본값으로 채워져 verbose 문서와 같은 hash를 낸다."""
    verbose = hydrate_strategy_document(yaml.safe_load(_read("quality_momentum.yaml")), identity=DRAFT_IDENTITY)
    minimal = hydrate_strategy_document(yaml.safe_load(_read("quality_momentum.minimal.yaml")), identity=DRAFT_IDENTITY)
    assert verbose.ok and minimal.ok
    assert strategy_spec_hash(minimal.spec) == strategy_spec_hash(verbose.spec) == QUALITY_MOMENTUM_SPEC_HASH
    assert minimal.spec.factors[0].label == minimal.spec.factors[0].factor_id


def test_schema_1_0_is_rejected() -> None:
    hydration = hydrate_strategy_document(yaml.safe_load(_read("quality_momentum.v1_0.yaml")), identity=DRAFT_IDENTITY)
    assert not hydration.ok
    assert [issue.code for issue in hydration.issues] == ["structure.unsupported_schema_version"]


def test_nested_factors_is_a_type_mismatch() -> None:
    document = yaml.safe_load(_read("quality_momentum.yaml"))
    document["factors"] = {"factors": document["factors"]}
    hydration = hydrate_strategy_document(document, identity=DRAFT_IDENTITY)
    assert not hydration.ok
    assert hydration.issues[0].pointer == "/factors"
```

- [ ] **Step 2: fixture 준비** — 현재 `quality_momentum.yaml`을 `quality_momentum.v1_0.yaml`로
  복사(내용 불변). `quality_momentum.yaml`을 1.1 verbose로 고친다(`schema_version: "1.1"`,
  `factors:` 바로 아래 `- factor_id:`, 노드마다 `kind:` 첫 줄, `signal`에서 `method` 제거는 P1-02이므로
  이 PR에서는 `signal: {}`가 아니라 기존 키 유지). `.json`, `.legacy.json`, `.unknown_key.yaml`도
  같은 모양으로. 새 `quality_momentum.minimal.yaml`:

```yaml
schema_version: "1.1"
title: "퀄리티 모멘텀"
data:
  start: "2021-01-01"
  end: "2026-08-31"
  universe_id: krx.common-stock
factors:
  - factor_id: momentum
    label: "모멘텀"
    direction: high
    weight: 0.6
    graph:
      nodes:
        - kind: field
          node_id: close
          field_id: price.close
        - kind: time_series
          node_id: mom_252
          operator: momentum
          input_node_id: close
          window: 252
      output_node_id: mom_252
signal:
  method: weighted_sum
portfolio:
  selection_count: 20
risk:
  max_name_weight: 0.05
execution:
  fee_bps: 15.0
```

  (`label`/`weight`가 verbose와 같은 값이어야 같은 hash다. `label` 생략 검증은 별도 fixture 없이
  Step 1 테스트 안에서 `document["factors"][0].pop("label")` 후 `label == factor_id`를 단언한다.)

- [ ] **Step 3: 테스트가 실패하는지 확인** — `cd backend && uv run pytest tests/contract/test_strategy_authoring_fixtures.py -q`.
  기대: `unsupported_schema_version` 등으로 FAIL.

- [ ] **Step 4: 모델 변경** — `_models.py`

```python
DEFAULT_FROM_FACTOR_ID = {"default-from": "factor_id"}


@dataclass(frozen=True, kw_only=True)
class DataStep:
    market: Market = Market.KRX
    start: date
    end: date
    universe_id: str = field(metadata=CATALOG_UNIVERSE)
    frequency: DataFrequency = DataFrequency.DAILY


@dataclass(frozen=True, kw_only=True)
class FactorSignal:
    factor_id: str = field(metadata=_factor_authoring("factor_id", identity=True))
    # 생략하면 hydrate가 factor_id를 넣는다(dataclass default가 아니라 파생 기본값).
    label: str = field(metadata={**_factor_authoring("label"), **DEFAULT_FROM_FACTOR_ID})
    direction: FactorDirection = field(metadata=_factor_authoring("preference"))
    weight: float = field(default=1.0, metadata={"authoring-default": 1.0})
    graph: FactorGraph = field(metadata=_factor_authoring("default_graph"))


@dataclass(frozen=True, kw_only=True)
class StrategySpec:
    identity: StrategyIdentity
    title: str
    description: str = ""
    data: DataStep
    eligibility: EligibilityStep = EligibilityStep()
    factors: tuple[FactorSignal, ...]
    signal: SignalStep = SignalStep()
    portfolio: PortfolioStep = PortfolioStep()
    risk: RiskStep = RiskStep()
    execution: ExecutionStep = ExecutionStep()
    parameters: tuple[ParameterDefinition, ...] = field(default=(), metadata=DEFINES_PARAMETER)
```

  `FactorStep` 삭제. 노드 dataclass는 그대로 둔다(positional 생성 호출이 registry·테스트에 많다).
  `kind` 우선 순서는 Step 5의 schema builder가 property 정렬로 만든다.

- [ ] **Step 5: hydrate·schema 변경**

  `_hydrate.py`: `SUPPORTED_SCHEMA_VERSIONS = ("1.1",)`. `_hydrate_dataclass`의 missing 처리:

```python
        elif "default-from" in field.metadata:
            derived_from[name] = str(field.metadata["default-from"])
        elif field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:
            _issue(...)
    ...
    for name, source in derived_from.items():
        if source in kwargs:
            kwargs[name] = kwargs[source]
```

  `_schema.py::dataclass_schema`: `"default-from" in field.metadata`이면 required에 넣지 않고
  `schema["x-default-from"] = field.metadata["default-from"]`. marker 목록에 `"default-from"` 추가
  (`factors`에는 `x-defines`를 두지 않는다 — 3절 P1-01 acceptance 참조). `_record_contract`의
  required 인자도 같은 조건.

- [ ] **Step 6: 경로·참조 갱신** — `spec.factors.factors` → `spec.factors`,
  `"factors.factors.{i}"` → `"factors.{i}"`, `"/factors/factors/{i}"` → `"/factors/{i}"`를
  `_validation.py`, `_explanation.py`, `_diff.py`, `_compiler.py`, `_service.py`(portfolio_design),
  `_trace_service.py`, `strategy_design/_service.py`(`FactorStep(...)` → tuple)에서 바꾼다.
  `facade/specification.py` export에서 `FactorStep` 제거.

  hash 알고리즘 golden 교체 — `test_strategy_authoring_fixtures.py`:

```python
# hash 알고리즘 golden: 모델과 무관하게 canonical 직렬화 규칙(sort_keys·최소 separator·allow_nan=False)만 고정.
# 값은 2026-09-04 1.0 fixture의 canonical payload를 그대로 literal로 옮긴 것이라 모델이 바뀌어도 변하지 않는다.
ALGORITHM_PAYLOAD_2026_09_04: dict[str, Any] = json.loads(_read("canonical_payload.v1_0.json"))
ALGORITHM_HASH_2026_09_04 = "9eb6872a3ca250dfb78b0887e5b236a98b24fb2ccdf2d6af0540218d49e998fe"


def test_hash_algorithm_is_unchanged() -> None:
    encoded = json.dumps(ALGORITHM_PAYLOAD_2026_09_04, ensure_ascii=False, allow_nan=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == ALGORITHM_HASH_2026_09_04
```

  `canonical_payload.v1_0.json`은 이 PR 시작 시점(1.0 모델)에서
  `canonical_strategy_payload(hydrate(quality_momentum.yaml))`을 dump해 만든다. `TEMPLATE_SPEC_HASH`
  golden은 제거한다(모델 shape golden은 `QUALITY_MOMENTUM_SPEC_HASH`가 맡는다).

- [ ] **Step 7: fixture 재생성·테스트 통과** — `uv run python tools/export_runtime_schema.py`,
  `uv run python scripts/export_openapi.py openapi.json`, `QUALITY_MOMENTUM_SPEC_HASH`를 새 값으로.
  `uv run pytest -q` 전체, `ruff`, `pyright` 통과.

- [ ] **Step 8: 커밋** — `feat(strategy): schema 1.1 모델 — factors 평탄화, 선택 보일러플레이트, kind 우선`

**Test plan**: Step 1의 3개 + `test_strategy_schema.py`에 `kind` 첫 property·`x-defines`·`x-default-from`
단언 + 기존 fixture/hydrate/validation/compiler 테스트 전체.

**Non-goals**: 미사용 필드·unary alias 제거(P1-02), 1.0 읽기·업그레이드(P1-03/04), frontend(P2).

**Rollback**: PR revert. 실 DB에 1.1 revision이 없는 한 데이터 영향 없음.

### P1-02 — 미사용 필드·중복 연산자 제거

**Intent**: spec D1의 S2·S4. 파이프라인이 읽지 않는 `signal.method`, `signal.entry_percentile`,
`execution.order_style`과 그 enum(`SignalMethod`, `OrderStyle`)을 제거하고, `UnaryOperator`에서
`rank/zscore/winsorize/neutralize`를 빼고 `CrossSectionalOperator.DEMEAN = "demean"`을 더한다.

**Acceptance**

- 세 필드를 쓴 문서는 `structure.unknown_key`로 거부된다.
- `unary: rank`는 `structure.invalid_enum`, `cross_sectional: demean`은 기존 `_unary`의 NEUTRALIZE
  분기와 같은 값을 낸다(평가 parity 테스트: 같은 입력에 대해 이전 `unary neutralize` 결과 golden ==
  새 `cross_sectional demean`).
- `STRATEGY_SCALAR_CONSTRAINTS`에서 `/signal/entry_percentile` 행과 코드
  `strategy.signal.percentile`, description key가 사라지고, "모든 코드에 owner가 하나" 테스트가 통과.
- `_evaluation.py::_unary`에 synthetic CrossSectionalNode 치환 코드가 없다.
- fixture `quality_momentum.*`에서 `signal.method`, `entry_percentile`, `order_style`을 제거해도
  hash golden이 갱신되고 4개 fixture가 같은 hash.
- `backend/openapi.json`, `runtime-schema.json` 재생성.

**예상 파일**: `_models.py`, `_constraints.py`, `_validation.py`(코드 registry), `domain/factor/_nodes.py`,
`_evaluation.py`, `_planning.py`/`_analytics.py`(operator 표), `facade/expression.py` export,
`adapters/outbound/engine_portfolio/_adapter.py`(참조 시), fixtures, `tests/domain/test_factor_*`,
`tests/domain/test_strategy_constraints.py`, `frontend/src/shared/config/messages.ts`의
`strategy.contract.signal.entry_percentile` key(frontend 쪽 문구 삭제는 P2-01에서).

**Test plan**: unknown key 3건, invalid enum 1건, demean parity 1건, registry owner 테스트, 전체 gate.

**Non-goals**: `time_series.lag`, `group.rank` 유지. frontend 변경 없음.

**Rollback**: PR revert.

### P1-03 — 1.0 → 1.1 업그레이드 변환과 동결 revision 읽기

**Intent**: spec D2·D3. `domain/strategy/_upgrade.py::upgrade_document_1_0(tree) -> tree`를 만들고
repository codec이 `schema_version = "1.0"` row를 이 변환으로 읽게 하며, 1.0 revision의
saved-reference backtest를 거부한다.

**Acceptance**

- `upgrade_document_1_0(yaml.safe_load(quality_momentum.v1_0.yaml)) == {**yaml.safe_load(quality_momentum.yaml), "signal": {}}`
  (제거된 `signal.method` 뒤에 빈 `signal` mapping이 남는다: 변환은 키를 지우지 않는다). 같은 hash.
  변환 항목: schema_version, `factors.factors` 평탄화, 세 필드 제거,
  `unary rank/zscore/winsorize` → `cross_sectional`, `unary neutralize` → `cross_sectional demean`,
  그 외 불변(기본값을 채우지 않는다).
- 1.0이 아닌 문서에 호출하면 `ValueError`.
- `decode_record`: row `schema_version == "1.0"`이면 (a) `sha256(spec_json) == spec_hash`,
  (b) source가 있으면 `sha256(source_text) == source_hash`, (c) `upgrade_document_1_0(payload)`가
  1.1로 hydrate, 세 검증 후 record를 돌려준다. record는 `spec.identity.schema_version == "1.0"`을
  유지한다(동결 표시). 변조 4종(spec_json 1바이트 변경, spec_hash 변경, source_hash 변경, hydrate
  불가 payload)은 `StrategyRepositoryStorageError`.
- 1.1 row는 기존 검증(재canonical 비교 + source 재컴파일) 그대로.
- `StrategyRevisionRecord`에 `requires_upgrade: bool` property(= schema_version != 최신).
- `backtest_run/_service.py`: saved reference가 가리키는 record가 `requires_upgrade`면
  `StrategyRevisionRequiresUpgradeError` → HTTP 422 `strategy_revision_requires_upgrade`.
- `strategy_design`의 legacy JSON API로 저장된 revision(`source=None`)의 generated source는 1.1
  canonical에서 생성(변경 없음, 테스트로 고정).
- SQLite fixture DB에 1.0 row를 심는 테스트 헬퍼(`tests/adapters/strategy_sqlite/_v1_0_rows.py`)로
  위를 검증한다. row 삽입은 저장소 코드가 아니라 테스트가 직접 SQL로 한다(1.0 인코더는 더 없다).

**예상 파일**: Create `domain/strategy/_upgrade.py`, `facade/document.py` export;
Modify `adapters/outbound/strategy_sqlite/_record_codec.py`, `strategy_memory` 동등 codec(있으면),
`application/strategy_design/ports/outgoing/strategy_repository.py`(`requires_upgrade`),
`application/backtest_run/_service.py`, `adapters/inbound/http_api/_app.py`(422 매핑),
`backend/openapi.json`; Tests 위 항목.

**Test plan**: 변환 golden 1, 항목별 단위 6, 비1.0 ValueError 1, codec 정상 1·변조 4, backtest 422 1,
legacy generated source 1.

**Non-goals**: source 텍스트(주석 보존) 변환(P1-04), frontend.

**Rollback**: PR revert. 1.0 row는 그대로 남고 P1-01 이후 상태(읽기 실패)로 돌아간다.

### P1-04 — `POST /strategy-documents/upgrade`

**Intent**: spec D3 source 경로. YAML/JSON 원문을 받아 주석·순서를 보존한 1.1 원문과 그 컴파일
결과를 돌려주는 endpoint.

**Acceptance**

- 요청 `{format, source}` → 응답 `{format, source, source_hash, compiled: CompiledDocument wire}`.
- YAML: `ruamel.yaml.YAML(typ="rt")`로 CST를 읽어 `_upgrade.py`와 **같은 규칙**을 CommentedMap에
  적용하고 dump. 규칙 함수는 `_upgrade.py`가 tree 연산으로 노출하는 `UPGRADE_STEPS`(순서 있는
  step 목록)를 CST 어댑터가 재사용한다. 두 벌의 규칙표를 두지 않는다.
- drift fail-closed: `safe_parse(upgraded_text) == upgrade_document_1_0(safe_parse(source))`가 아니면
  500이 아니라 422 `document_upgrade_drift`(진단 메시지에 첫 불일치 pointer).
- 1.0이 아닌 source → 422 `document_not_upgradeable`. 구문 오류 → 기존 syntax 진단으로 422.
- JSON: `json.loads` → dict 변환 → 원문의 들여쓰기 폭(2 또는 4, 감지 실패 시 2)으로 `json.dumps`.
- golden: `quality_momentum.v1_0.commented.yaml`(주석·빈 줄·키 순서가 섞인 1.0)이
  `quality_momentum.v1_1.commented.yaml`과 바이트 동일하게 변환된다.
- adapter 위치: CST 변환은 `adapters/outbound/document_codec`(ruamel owner)에 두고 application
  port `DocumentUpgradePort`로 노출한다. domain은 ruamel을 import하지 않는다.
- OpenAPI 재생성(frontend SDK는 P2-02에서).

**예상 파일**: Modify `domain/strategy/_upgrade.py`(step 목록 노출), `application/strategy_authoring/ports/outgoing/document_codec.py`,
`_service.py`(upgrade use case), `adapters/outbound/document_codec/_codec.py`(+`_upgrade_cst.py`),
`adapters/inbound/http_api/_app.py`, fixtures 2, tests.

**Test plan**: golden 1, drift 주입(step 목록 monkeypatch) 1, 비1.0 1, 구문오류 1, JSON 들여쓰기 2, HTTP 3.

**Rollback**: PR revert(endpoint 삭제).

### P1-05 — 적용 불가 필드 경고

**Intent**: spec D4. `FIELD_APPLICABILITY` 표와 compile warning `strategy.field.inapplicable`,
runtime schema `x-applicable-when`.

**Acceptance**

- `_constraints.py`에 `FieldApplicability(pointer, applies: Callable[[StrategySpec], bool], condition_key, description)`
  행 7개(spec D4 표). `SEMANTIC_ONLY_CODES`에 `strategy.field.inapplicable`.
- `StrategyAuthoringService.compile`: hydrate·validate 성공 후 parse tree에 pointer가 존재하고
  `applies(spec)`가 False면 warning 진단(pointer, range=key range) 추가. 생략된 필드는 경고 없음.
  warning이므로 `CompiledDocument.ok`는 유지.
- runtime schema: 해당 property에 `x-applicable-when: {"pointer": "/portfolio/side", "equals": "long_short"}`
  같은 선언적 조건. 표의 predicate와 선언이 어긋나지 않도록 행이 `condition` 데이터(pointer+equals
  또는 pointer+not_null)를 소유하고 predicate는 그 데이터에서 파생한다.
- 진단 message는 한글, description_key는 `strategy.contract.applicable.<field>`.

**예상 파일**: `_constraints.py`, `_validation.py`(코드 registry), `_schema.py`(`x-applicable-when`),
`application/strategy_authoring/_service.py`, tests, `runtime-schema.json`.

**Test plan**: 7행 각각 명시+불일치 → warning, 명시+일치 → 없음, 생략 → 없음; schema 선언 7개;
predicate가 선언에서 파생됨(행마다 `condition`으로 재계산한 결과 == predicate).

**Rollback**: PR revert.

**Phase 1 exit**: P1-01~05 MERGED. SoT·책임분리 점검 서브에이전트(Opus) 실행·기록.
`.claude/rules/strategy-workbench-sot.md`에 "업그레이드 변환 owner", "적용 조건표 owner" 행 추가.

---

## 4. Phase 2 — frontend 1.1

### P2-01 — generated SDK와 1.1 pointer 적응

**Intent**: `npm run api:generate`로 SDK를 1.1로 갱신하고 `factors.factors`를 쓰는 frontend 코드와
테스트를 옮긴다.

**Acceptance**

- `use-execution-plans.ts`: `factorGraphPointer(i) === "/factors/${i}/graph"`,
  `factorIndexAtPointer("/factors/3/graph/nodes/1") === 3`.
- `canonical-snippets.ts`: 팩터 collection을 키 이름이 아니라 항목의 `x-authoring-identity`
  마커로 찾는다(P1-01 조정: `factors`에 `x-defines`를 두지 않음). 마커가 붙은 루트 배열이 둘 이상이면
  fail-closed. 팩터 스니펫은 root sequence 항목으로 삽입되며 `factors` 키가 없으면 `factors:` + 항목을
  삽입한다. 스니펫 value에 `label`은 catalog label, `weight`는 `x-authoring-default`.
- `strategy-debugger-context.ts`, `execution-plan-panel.tsx`, `factor-graph-panel.tsx`,
  `strategy-outline.ts`가 `spec.factors`를 쓴다.
- `messages.ts`에서 `entry_percentile`·`order_style`·`method` 계약 문구 제거.
- 단위 테스트 fixture 1.1(`runtime-schema.json`·`quality_momentum.*`는 backend 공유 fixture를
  `readBackendFixture`로 읽는다, 복사본 없음). `npm test`·typecheck·lint·build 통과.
- P1-02의 `strategy.signal.percentile` 문구 제거.

**예상 파일**: generated 17 files(줄 수 제외), 위 5 src + 14 test files.

**Non-goals**: e2e(P2-02), 업그레이드 UI(P2-02).

### P2-02 — 1.0 문서 업그레이드 UI와 e2e 1.1

**Intent**: 1.0 revision을 열었을 때 배너와 "1.1로 업그레이드" 동작, e2e spec·fixture 1.1.

**Acceptance**

- compile 진단에 `structure.unsupported_schema_version`이 있으면 편집기 위에
  `UpgradeBanner`(feature `edit-strategy/ui/upgrade-banner.tsx`) 표시. frontend는 은퇴 버전 문자열을
  갖지 않는다(Phase 2 감사 DEFECT-P2X-002 조정): 변환 불가 버전은 endpoint의 422
  `strategy_document.not_upgradeable` 문구로 드러난다.
- 클릭 → `POST /strategy-documents/upgrade`(TanStack mutation) → 응답 source를
  `CodeEditorHandle.setText`로 적용(undo 1단계) → 문서가 dirty·1.1 compile 흐름.
- 실패(422 drift/not_upgradeable/네트워크)는 배너 안 오류 문구, source 불변.
- legacy_json 동결 row(`generated: true`, `requires_upgrade: true`)는 generated source가 이미 1.1이라
  업그레이드 endpoint를 부르지 않는다. 배너는 "새 revision으로 저장"만 제안한다(P1-03 리뷰 잔여 위험 1).
- 목록·history 화면은 `StrategySummary.requires_upgrade`·`RevisionSummary.requires_upgrade`로 동결 표시.
- revision page의 saved reference backtest가 422 `strategy_revision_requires_upgrade`를 받으면
  같은 배너로 안내(run controls는 disabled + 이유).
- `workbench.workflow.spec.ts`: 1.1 fixture로 갱신 + 시나리오 "1.0 revision 열기 → 업그레이드 →
  저장 → backtest"(1.0 row는 테스트 fixture DB seeding으로 준비).
- i18n ko/en.

**Test plan**: MSW 성공/실패 3, reducer(setText 후 dirty) 1, e2e 1.

### P2-03 — 적용 조건 표시

**Intent**: Contract Inspector와 현 read-only Form에 `x-applicable-when`과
`strategy.field.inapplicable` 경고를 표시한다. P4의 Form 편집이 같은 projection을 쓴다.

**Acceptance**

- `contract-inspector.ts`의 `ContractFieldProjection`에 `applicableWhen: {pointer, equals|notNull} | null`,
  `applicable: boolean | null`(parse tree로 판정).
- Inspector가 "현재 `portfolio.side: long_only`에서는 읽히지 않음" 문구 표시.
- Problems panel이 warning을 기존 경로로 보여줌(코드 → 메시지 매핑만 추가).

**Phase 2 exit**: SoT·책임분리 점검. e2e green.

---

## 5. Phase 3 — source 트랜잭션

### P3-01 — `source-transactions.ts`

**Intent**: spec D5 원시 연산 4종을 순수 함수로 구현한다. 편집기와 React를 모른다.

**Acceptance**

```ts
export type SourceOperation =
  | { kind: "replace-scalar"; pointer: string; value: Scalar }
  | { kind: "insert-key"; parentPointer: string; key: string; value: unknown }
  | { kind: "insert-item"; parentPointer: string; value: unknown; index?: number }
  | { kind: "remove"; pointer: string };
export type Scalar = string | number | boolean | null;
export type PlannedEdit = { from: number; to: number; insert: string; nextSource: string; selection: { from: number; to: number } };
export type PlanFailure = "yaml-only" | "not-found" | "exists" | "not-scalar" | "not-mapping" | "not-sequence" | "parse";
export const planSourceOperation = (source: string, format: SourceFormat, op: SourceOperation): { status: "ok"; edit: PlannedEdit } | { status: "error"; reason: PlanFailure };
```

- `replace-scalar`: `valueRanges[pointer]` 범위를 `yaml.stringify(value)` 한 줄 literal로 교체.
  대상이 mapping/sequence면 `not-scalar`. 문자열은 `yaml`이 필요할 때만 따옴표(예: `"1.0"`, `yes`,
  빈 문자열).
- `insert-key`: 부모 value 범위의 마지막 줄 다음에 부모 들여쓰기 + 2칸으로 `key: value` fragment.
  부모가 flow `{}`면 value 범위를 block mapping으로 교체. 키가 이미 있으면 `exists`.
- `insert-item`: 부모 sequence 끝(또는 `index`) 에 `- ...` fragment. `[]`면 block sequence로 교체.
- `remove`: key range 시작 줄부터 value range 끝 줄까지 삭제. 삭제로 부모 mapping이 비면
  `key: {}`, sequence가 비면 `key: []`로 남긴다(구조 유지).
- 들여쓰기 폭은 문서에서 감지(mapping 아래 첫 중첩 키의 열 → 없으면 첫 시퀀스 항목의 `-` 열 → 2;
  빈 컨테이너 확장에만 쓰이고 일반 삽입은 형제의 실제 열을 복사), EOL은 `\r\n`/`\n` 다수결.
  모든 연산은 `parseSource(nextSource).status === "ok"` preflight.
- 알려진 제한(P3-01 리뷰): 내용이 있는 flow 컬렉션(`{x: 1}`, `[1, 2]`) 안은 편집하지 않는다(fail-closed,
  사유는 `parse`/`not-sequence`/`not-found`). 삭제된 키/항목 **위**의 독립 주석은 그 자리에 남는다(어느 키의
  주석인지 YAML이 답하지 않으므로 보수적으로 보존). 예외는 삭제로 부모가 비어 `{}`/`[]`로 접힐 때뿐이다:
  그 컨테이너 안에 있던 독립 주석은 함께 사라진다(property test의 주석 소유자 규칙: 키 줄과 항목 줄이
  소유자이고, `remove`는 대상 pointer와 그 아래가 소유한 주석만 지울 수 있다). 부모 `key:` 줄의 줄 끝
  주석은 남고 빈 컨테이너가 다음 줄로 간다(`a: # 메모` → `a: # 메모\n  {}`; P3-02 리뷰 P2-2). 줄 끝
  주석은 property의 `commentLines`(독립 주석 줄만 셈)가 보지 않으므로 단위 테스트가 지킨다.
- P3-02에서 확장(P3-01 알려진 제한 해소): 값이 비어 있는 `key:`(`factors:` → null)는 삽입 연산의 부모로
  쓰일 때 빈 컨테이너로 본다(`insert-key`면 `{}`, `insert-item`이면 `[]`, 줄 끝 주석은 그대로 두고 그
  줄 끝 뒤에 block을 연다). 내용이 전혀 없는 문서(빈 줄·주석뿐, parser는 거부)는 **루트 `insert-key`에
  한해** 빈 mapping으로 본다(새 문서·스니펫 첫 삽입). 삽입 자리 규칙은 키와 항목이 **같다**: 새 키/항목은
  **앞 형제의 내용 줄 끝 뒤**에 들어간다(`insert-key.before`·`insert-item.index`; 앞 형제가 없으면 부모
  `key:` 줄 끝 뒤, 루트 첫 키 앞이면 문서 시작). 그래서 대상 형제 위의 독립 주석은 계속 그 대상을
  설명한다. `- key:`/`- - x`처럼 dash 줄에 붙은 첫 키/항목 앞에는 그 자리에 들어가고 기존 것이 다음 줄로
  밀린다(P3-02 리뷰 P2-1로 통일; P3-01의 "대상 `-` 자리" 규칙은 폐기). `planSourceOperation`은
  `options.anchor`(줄 시작 offset 또는 문서 끝)를 받아 형제 순서가 허용하는 구간(앞 형제 줄 끝 ~ 대상 줄
  시작) 안이면 그 자리에 넣는다 — 스니펫이 커서 줄 자리를 지키는 데 쓴다(P3-02 리뷰 P1-1: 선행 주석·빈 줄
  위로 올라가지 않는다). `- - x` 안쪽 첫 항목 삭제는 dash 줄 첫 키와 같은 규칙(자기 내용 줄 끝까지 지우고
  다음 줄 들여쓰기를 걷음)이라 사이 주석이 남는다(2차 리뷰 P2-R1 대안 2). 이 갈래의 회귀 방지는 단위
  테스트가 맡고, property는 주석 소유자 규칙을 문서 구조로 고정하는 역할이다(생성기는 줄마다 약 1/3
  확률로 주석을 넣어 두 항목 사이·첫 형제 위·머리말 주석 모양이 자주 나온다).
- 범위는 `parseSource`의 `valueRanges`/`keyRanges`에서 **정확히 그 pointer로** 읽는다.
  `locateRange`는 pointer가 없으면 조상 범위로 fallback하므로 `replace-scalar`·`remove`에 쓰면 부모
  전체를 지운다. 없는 pointer는 `not-found`다(Phase 2 감사 4.5). mapping/sequence 노드의 range는
  뒤따르는 주석 줄까지 포함할 수 있으므로 삽입·삭제 경계는 leaf(스칼라·빈 컨테이너)·키 범위의
  최댓값으로 잡는다.
- property test(`fast-check` 추가): 임의 1.1 문서 생성기 × 임의 유효 연산에 대해
  `parseSource(nextSource).tree`가 tree 연산(`applyToTree`) 결과와 deep-equal이고, 연산 범위 밖의
  줄은 바이트 동일.

**예상 파일**: Create `features/edit-strategy/model/source-transactions.ts`, `__tests__/source-transactions.test.ts`,
`__tests__/source-transactions.property.test.ts`, `shared/testing/strategy-document-arbitrary.ts`.
devDependency `fast-check`.

**Non-goals**: 편집기 연결, JSON 편집.

### P3-02 — `useSourceTransactions`와 스니펫 재구성

**Intent**: 훅이 editor handle에 연산을 적용하고 feedback을 낸다. `useSnippetInsertion`을 이 훅과
`insert-key`/`insert-item` 위에 다시 만든다.

**Acceptance**

```ts
export type SourceTransactions = {
  apply: (op: SourceOperation, label: string) => void;
  feedback: TransactionFeedback;   // idle | applied(label) | error(label, reason)
  onEditorReady: (editor: CodeEditorHandle | null) => void;
  enabled: boolean;               // yaml && !composing && parse ok && editor ready
};
export const useSourceTransactions = (state: DocumentState, editorActive?: boolean): SourceTransactions;
```

- `apply`는 `planSourceOperation(editor.getText(), ...)` → `replaceRange(from, to, insert, selection)`.
  `state.composing`이면 `composing` 오류. feedback scope는 `documentEpoch`로 초기화(기존 스니펫과 동일).
- 스니펫: `canonical-snippets.ts`의 fragment 문자열 조립(`fragmentFor`, `indentFragment`)을 제거하고
  `planSnippetEdit`는 커서 context를 `insert-key`/`insert-item` 연산으로 번역만 한다. 기존 스니펫
  테스트 전부 통과(동작 동일).
- 구현 결정(P3-02): 훅은 `run(planner, label)`도 낸다 — 편집기 현재 텍스트·선택으로 계획하는 함수를
  같은 적용 경로(`replaceRange` 한 번, scroll, focus, feedback)로 태운다. `apply(op)`는
  `run(({text}) => planSourceOperation(text, "yaml", op))`이고 스니펫은 `run(({text, selection}) =>
  planSnippetEdit(...))`이다. 스니펫이 연산 하나로 표현되지 않는 이유: 커서 줄에 반쯤 입력한 키(`sig`)를
  빼고 나서 연산을 계획해야 하고, 결과는 그 키까지 포함한 단일 범위 편집(history 한 번)이어야 한다.
  `planSnippetEdit`는 (1) 중복 판정(원문 전체, 커서 문맥보다 먼저; 원문이 parse되지 않으면 커서 줄을 뺀
  원문으로 — 그래서 parse 실패 문서의 섹션 중복은 예전 `duplicate` 대신 `parse`로 보고될 수 있다, 안전
  방향) (2) 커서 줄을 뺀 원문과 그 줄의 자리(`anchor`) (3) 커서 줄 뒤에 오던 첫 형제 → `before`, 앞에
  있던 항목 수 → `index` (4) `planSourceOperation(stripped, op, { eol, anchor })` (5) 원문 대비 단일
  범위 diff(접두는 키 시작까지, 접미는 커서 줄 끝부터)를 한다. EOL은 원문에서 재고 `options.eol`로
  넘긴다(커서 줄을 빼면 한 줄 문서가 되어 EOL 정보를 잃는 경우). feedback scope는
  `documentEpoch` + 호출자 `scope`(스니펫은 카탈로그 status)다. 계획은 편집기 `getText()`로 세우므로
  연산 1회 = parse 2회(계획·preflight)이고 호출은 keystroke가 아니라 확정(blur·Enter·버튼) 시점만이다
  (P3-01 리뷰 잔여 위험의 처리 방식; P4-02 필드 편집이 이 규칙을 따른다).

**Phase 3 exit**: SoT 점검(정본이 source 하나인지, 프론트에 필드 목록이 없는지).

---

## 6. Phase 4 — Form 편집

### P4-01 — `form-projection.ts`

**Intent**: spec D6. runtime schema × parse tree × diagnostics → 섹션/필드 목록 순수 projection.

**Acceptance**

```ts
export type FormControl =
  | { kind: "text" } | { kind: "number"; min: Bound | null; max: Bound | null; integer: boolean }
  | { kind: "date" } | { kind: "boolean" } | { kind: "enum"; values: readonly string[] }
  | { kind: "catalog"; catalog: "equity-field" | "universe" | "factor" | "subgraph" }
  | { kind: "reference"; namespace: "node" | "parameter"; candidates: readonly string[] };
export type FormField = {
  pointer: string; templatePointer: string; key: string;
  control: FormControl; nullable: boolean; required: boolean;
  written: boolean; value: unknown; defaultValue: unknown; hasDefault: boolean;
  applicable: boolean | null; unit: string | null; displayUnit: string | null; descriptionKey: string | null;
  diagnostics: DocumentDiagnostic[];
};
export type FormSection =
  | { kind: "object"; pointer: string; key: string; written: boolean; fields: FormField[] }
  | { kind: "list"; pointer: string; key: string; itemSchemaPointer: string; items: { pointer: string; summary: string; fields: FormField[]; branches: readonly string[] | null }[] };
export const projectForm = (schema: JsonSchema, parse: ParsedSource | null, diagnostics: DocumentDiagnostic[]): FormProjection;
```

- 섹션 순서는 `schema.properties` 순서. `factors` 항목의 `graph` 필드는 `{kind: "graph-link"}`로
  표시만(편집 불가).
- 컨트롤 결정 규칙은 `contract-inspector.ts`가 이미 하는 스키마 해석(`schemaAt`, `x-catalog`,
  `x-reference`, enum, format)을 재사용한다. 규칙을 두 벌 두지 않기 위해 공통 함수를
  `schema-navigator.ts`로 내린다.
- `written`은 parse tree 존재 여부, `value`는 tree 값, 없으면 `defaultValue`.
- 테스트: `runtime-schema.json` fixture로 verbose/minimal 문서 각각 필드 수·written·control 종류·
  diagnostics 매핑을 단언.

### P4-02 — Form 컨트롤과 트랜잭션 연결

**Intent**: 편집 가능한 Form 패널. `StrategyProjectionPanel`의 `form` view를
`StrategyFormPanel`(feature `ui/strategy-form-panel.tsx`)로 대체한다.

**Acceptance**

- 컨트롤별 커밋 규칙: number/text/date는 blur 또는 Enter에서 값이 바뀌었을 때만 1 트랜잭션,
  enum/boolean/catalog/reference는 변경 즉시 1 트랜잭션. Escape는 입력 취소.
- `written === false` 필드는 placeholder(기본값, 회색). 값 입력 → `insert-key`. "기본값으로"
  버튼 → `remove`.
- nullable 필드는 "설정 안 함" 옵션 → `replace-scalar null`(written) 또는 `remove`.
- 카탈로그 picker는 기존 `useSchemaAssist`의 catalog query(`equityCatalog`, universe)를 props로
  받는다. feature가 entities/shared만 import.
- 필드에 diagnostics badge(error/warning)와 `applicable === false` 안내.
- 헤더: "YAML source에 바로 반영 · undo 가능" 문구, `enabled === false`면 이유(`JSON 문서`,
  `구문 오류`, `IME 입력 중`)와 함께 컨트롤 disabled.
- 접근성: 모든 컨트롤 `label` 연결, role/name으로 테스트.
- 테스트: 사용자 이벤트로 값 변경 → editor handle mock의 `replaceRange` 인자와 `nextSource` 단언;
  MSW로 compile 왕복 후 badge 갱신.

**Non-goals**: 목록 섹션(P4-03), 실제 page 연결(P4-04).

### P4-03 — 목록 섹션

**Intent**: eligibility rules, parameters, factors 헤더의 추가·삭제·편집.

**Acceptance**

- 항목 추가: 스키마에서 `materializeSchemaValue`(P4-05 스니펫 모듈의 함수, `schema-navigator.ts`로
  이동)로 최소 항목 → `insert-item`. parameters는 `kind` 선택 다이얼로그 후 분기 스키마로 materialize.
- factors: "카탈로그에서 추가" 메뉴가 기존 팩터 preset 스니펫 목록을 보여주고 선택 시
  `insert-item`. 빈 factor(직접 작성)도 가능(`graph`는 최소 field node 1개).
- 삭제: `remove`. factors 삭제 시 다른 곳의 참조(`saved_factor` 노드 `factor_id`)가 있으면 참조
  목록을 보여주고 거부(D7 삭제 가드와 같은 `findReferences(tree, namespace, id)` 유틸, `model/document-references.ts`).
- 각 항목의 필드는 P4-02 컨트롤 재사용. factor 항목의 `graph`는 "Graph에서 열기" 버튼(view=graph,
  pointer 선택).
- 테스트: 추가/삭제/참조 거부/kind 분기.

### P4-04 — IDE·page 연결, 잠금, e2e

**Intent**: new/revision page가 Form 패널을 연결하고 view 전환·stale·잠금·i18n·e2e를 마감한다.

**Acceptance**

- `new-strategy-page.tsx`, `strategy-revision-page.tsx`의 `projections.form`이 `StrategyFormPanel`.
  editor handle은 page가 `useSourceTransactions`를 한 번 만들어 Form·Graph·스니펫에 공유.
- Form view가 활성일 때도 hidden editor가 살아 있어 트랜잭션이 적용되고, YAML view로 돌아가면
  주석·순서가 그대로다(테스트: 주석이 있는 fixture → Form에서 값 변경 → source 문자열에 주석 유지).
- syntax-invalid: Form은 마지막 valid parse 값을 STALE badge로 보여주고 컨트롤 disabled.
- JSON 문서: 컨트롤 disabled + "YAML로 변환 후 편집" 안내(기존 포맷 명령 링크).
- Command palette에 "Form에서 편집" 없음(view 전환 명령만). 키보드: 섹션 접기/펼치기.
- e2e: Form에서 `risk.max_name_weight` 변경 → 저장 → hash가 YAML 직접 편집과 동일; factor 추가 →
  plan 갱신.
- `.claude/rules/strategy-workbench-sot.md` 전략 의미 행 개정, `frontend-testing.md` round-trip 문구.

**Phase 4 exit**: SoT·책임분리 점검(Form이 필드 목록·기본값·검증을 복제하지 않는지).

---

## 7. Phase 5 — Graph 편집

### P5-01 — `graph-transactions.ts`

**Intent**: spec D7. 팩터 하나의 `graph` pointer 아래 편집을 `SourceOperation`으로 번역하는 순수 모듈.

**Acceptance**

```ts
export const addNode = (tree, factorPointer, kind, schema): { op: SourceOperation; nodeId: string } | { error: "unknown-kind" };
export const setNodeField = (tree, nodePointer, key, value): SourceOperation;
export const rewireInput = (tree, nodePointer, inputKey, targetNodeId): SourceOperation | { error: "self" | "not-found" };
export const setOutput = (tree, factorPointer, nodeId): SourceOperation;
export const removeNode = (tree, factorPointer, nodeId): SourceOperation | { error: "referenced"; by: string[] };
export const setMissingPolicy = (tree, factorPointer, policy): SourceOperation;
export const suggestNodeId = (tree, factorPointer, base: string): string;   // base, base_2, base_3 …
```

- `addNode`는 `$defs/<Kind>Node` 분기 스키마로 최소 항목을 materialize하고 `kind` 첫 키.
  reference 필드는 빈 문자열이 아니라 그래프의 마지막 노드 id로 채운다(즉시 valid 가능).
- `removeNode`는 `findReferences`로 `*_node_id`와 `output_node_id` 참조를 검사.
- 테스트: 각 함수 정상 1·오류 1, `suggestNodeId` 유일성.

### P5-02 — Graph UI

**Intent**: 기존 `FactorGraphPanel`(backend plan 순서 DAG list)에 편집 컨트롤을 더한다.

**Acceptance**

- 팩터 헤더: "노드 추가" 메뉴(kind 목록은 스키마 `oneOf` 분기에서), `output_node_id` select,
  `missing_policy` select.
- 노드 카드 선택 → 오른쪽 property editor(P4-02 컨트롤 재사용; `x-reference: node` 필드는 같은
  그래프의 다른 노드 id select). 입력 슬롯 버튼 → 대상 노드 select → `rewireInput`.
- 삭제 버튼 → 참조가 있으면 참조 목록 안내, 없으면 `remove`.
- 편집 후 backend plan이 갱신될 때까지 카드에 "재계산 중" 상태(기존 loading state 재사용).
- 키보드: 노드 목록 roving tabindex 유지, 메뉴는 `Menu` primitive.
- 테스트: 사용자 이벤트 → `replaceRange` 인자, 삭제 가드, plan 갱신 MSW.

### P5-03 — 연결·e2e·문서·규칙 마감

**Acceptance**

- page 연결(Form과 같은 `useSourceTransactions` 공유), Form의 "Graph에서 열기" ↔ Graph의
  "Form에서 열기" 왕복.
- e2e: 노드 추가 → 재연결 → 출력 지정 → plan 갱신 → 저장.
- 문서: ADR 2026-09-04 머리말 개정 링크·D5 개정 표기, `WORKFLOW.md` 2.2 예시 1.1,
  `docs/manual/strategy-workbench/README.md`, `README.md`, `frontend/README.md`, `backend/FACTORS.md`,
  로드맵 M8 "Graph 직접 편집" 체크.
- 기존 `projection.readOnly`, `graph.readOnly` 문구·코드 제거.

**Phase 5 exit**: SoT·책임분리 최종 점검. 전체 e2e green. initiative COMPLETE.

---

## 8. Phase 종료 gate

- 모든 PR에 reviewer APPROVE 기록.
- backend·frontend·root 전체 gate 통과, generated OpenAPI/SDK diff 0.
- Phase마다 Opus 서브에이전트 SoT·책임분리 감사 결과와 조치를 PLAN.md Phase exit에 기록.
