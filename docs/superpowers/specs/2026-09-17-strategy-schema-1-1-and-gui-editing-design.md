# 설계: StrategySpec schema 1.1과 Form/Graph 편집

> 작성: 2026-09-17
>
> 상태: Accepted (제품 소유자 결정 2026-09-17: "schema 1.1까지 전부", "Form과 Graph 한 번에,
> stacked PR로 범위를 나눈다")
>
> 선행 계약: [Strategy Authoring Contract ADR](./2026-09-04-strategy-authoring-contract-adr.md)
> (D2 verbose 문법, D3 hash, D5 read-only projection을 이 문서가 개정한다)
>
> PR 진행은 [docs/planning/strategy-gui-editing/PLAN.md](../../planning/strategy-gui-editing/PLAN.md)

## 1. 맥락

YAML Strategy Workbench initiative(52 PR)가 끝난 뒤 두 가지 요구가 남았다.

1. 전략 YAML 규칙에 군더더기가 있다. `factors.factors` 이중 키, 빈 섹션을 강제하는 필수 키,
   파이프라인이 읽지 않는 필드, 같은 연산을 두 가지로 쓰는 그래프 연산자가 그것이다.
2. Form과 Graph가 읽기 전용 projection이라 GUI로 전략을 쓸 수 없다. 편집기 사용자가 아닌
   사람은 YAML을 직접 쳐야 한다.

2026-09-17 점검에서 확인한 사실은 다음과 같다.

| 관찰 | 근거 |
|---|---|
| `description`, `eligibility`, `signal`, `portfolio`, `risk`, `execution`를 빠뜨리면 structural error | `hydrate_strategy_document` 직접 실행. 각 섹션의 모든 필드가 기본값인데도 섹션 키가 필수 |
| 팩터 `label`, `weight`가 필수 | `FactorSignal`에 dataclass default가 없음. `weight`는 `authoring-default: 1.0` 메타데이터만 있음 |
| `signal.method`, `signal.entry_percentile`, `execution.order_style`을 읽는 코드가 없음 | 모델·스키마·검증·설명 파일을 제외한 `application/`, `domain/`, `adapters/`에서 참조 0건 |
| `unary.rank/zscore/winsorize`는 `cross_sectional` alias | `domain/factor/_evaluation.py:_unary`가 `CrossSectionalNode`로 치환해 평가 |
| `unary.neutralize`는 cross-sectional demean | 같은 함수. `group.neutralize`와 이름만 같고 의미가 다름 |
| `portfolio` 12개 필드 중 모드에 따라 읽히지 않는 필드가 6개 | `short_selection_count`(long_short), `selection_percentile`(percentile), `rebalance_every_n_sessions`(every_n_sessions), `liquidity_*`, `risk.sector_neutral`(long_short), `risk.risk_field_id`(weighting=risk), `signal.regime_*` |
| 저장된 revision은 UPDATE/DELETE 불가 | `strategy_sqlite/_schema.py` immutable trigger. `spec_json`은 canonical compact JSON, `spec_hash`는 그 바이트의 sha256 |

## 2. 결정

### D1. schema_version 1.1 authoring 문법

1.1 문서는 다음 여섯 가지가 1.0과 다르다. 나머지 키 이름·값·중첩은 1.0과 같다.

| # | 변경 | 1.0 | 1.1 |
|---|---|---|---|
| S1 | 팩터 목록 평탄화 | `factors: {factors: [...]}` | `factors: [...]` |
| S2 | 미사용 필드 제거 | `signal.method`, `signal.entry_percentile`, `execution.order_style` | 키 자체가 unknown key |
| S3 | 보일러플레이트 생략 허용 | `description`, `eligibility`, `signal`, `portfolio`, `risk`, `execution`, 팩터 `label`·`weight`, `data.market` 필수 | 전부 선택. 생략 시 기본값. `label` 기본값은 `factor_id` |
| S4 | 그래프 연산자 정리 | `unary: rank/zscore/winsorize/neutralize` | `unary`는 `negate`, `lag`만. 순위·표준화·윈저화는 `cross_sectional`, demean은 `cross_sectional: demean` |
| S5 | 노드 `kind` 우선 | 스키마·스니펫·fixture가 `kind`를 맨 뒤에 둠 | dataclass를 `kw_only`로 바꿔 `kind`를 첫 필드로 선언. 스키마 property 순서, 스니펫, fixture가 따라감 |
| S6 | 모드 불일치 경고 | 읽히지 않는 필드를 써도 조용히 무시 | 컴파일이 `strategy.field.inapplicable` warning을 pointer와 함께 반환 |

최소 1.1 문서:

```yaml
schema_version: "1.1"
title: "퀄리티 모멘텀"
data:
  start: "2021-01-01"
  end: "2026-08-31"
  universe_id: krx.common-stock
factors:
  - factor_id: momentum
    direction: high
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
portfolio:
  selection_count: 20
risk:
  max_name_weight: 0.05
```

- canonical payload는 여전히 모든 기본값을 채운다. 생략은 source 편의일 뿐 `spec_hash`에 영향이
  없다. 같은 의미의 1.1 문서는 키를 생략하든 명시하든 같은 hash다.
- `time_series.lag`는 유지한다. `unary: lag` 노드를 하나 더 두는 것과 결과는 같지만, 창 계산의
  편의 파라미터이지 같은 개념의 두 번째 표기는 아니다.
- `group.rank`는 유지한다. 그룹 내 순위는 `cross_sectional.rank`와 다른 연산이다.
- `data.market`, `data.frequency`, `execution.timing`은 선택지가 하나뿐이므로 선택 필드다.
  제거하지 않는 이유는 KRX 외 시장이 로드맵에 있어서다.
- `SUPPORTED_SCHEMA_VERSIONS = ("1.1",)`. 1.0은 새 문서로 받지 않는다. 1.0 문서는 D3의
  업그레이드 경로로만 들어온다.
- hash 알고리즘(ADR D3: sort_keys, 최소 separator, `allow_nan=False`, sha256)은 바꾸지 않는다.
  payload 모양이 바뀌었으므로 같은 전략의 1.0 hash와 1.1 hash는 다르다. 이것이 schema_version을
  올리는 이유다.

### D2. 저장된 1.0 revision은 동결 이력이다

revision row는 immutable trigger로 보호되므로 재해시나 in-place 변환을 하지 않는다.

- **읽기**: `schema_version = "1.0"` row는 repository codec이 D3의 dict 변환으로 payload를
  1.1로 올린 뒤 hydrate한다. 무결성 검증은 (a) `spec_hash == sha256(spec_json bytes)`,
  (b) `source_hash == sha256(source_text)`, (c) 업그레이드된 payload가 1.1로 hydrate된다, 세
  가지로 대체한다. 1.0 모델이 더 이상 없으므로 "source가 stored spec으로 컴파일된다"는 검증은 1.0
  row에 한해 생략한다. row가 immutable이고 저장 시점에 이미 검증되었기 때문이다.
- **표시**: history, revision 화면, diff, run history는 1.0 revision을 그대로 보여준다. 표시되는
  `spec_hash`는 저장된 1.0 hash다. 재계산하지 않는다.
- **실행**: 1.0 revision을 saved reference(`strategy_id + revision + expected_spec_hash`)로
  backtest하는 요청은 `422 strategy_revision_requires_upgrade`로 거부한다. 업그레이드된 spec의
  hash가 저장된 hash와 다르므로 run manifest의 provenance가 성립하지 않는다.
- **편집**: 1.0 revision을 편집기에서 열면 컴파일이 `structure.unsupported_schema_version`
  structural error를 돌려주고, 프론트는 "1.1로 업그레이드" 동작을 제공한다. 업그레이드는 source를
  바꾸므로 dirty가 되고, 저장하면 새 revision(1.1 hash)이 생긴다. 이 revision부터 실행 가능하다.
- **legacy_json revision**(source 없음)은 서버가 canonical payload에서 생성하는 source를 1.1
  모양으로 바로 내보낸다. 주석이 없으므로 잃을 것이 없다.
- **JSON spec API**(`strategy_design`)는 모델과 함께 1.1 모양으로 바뀐다. generated SDK를
  같은 PR에서 갱신한다.

### D3. 1.0 → 1.1 업그레이드 변환은 domain이 한 벌만 소유한다

`domain/strategy/_upgrade.py`의 `upgrade_document_1_0(tree) -> tree`가 유일한 변환 정의다.

1. `schema_version` "1.0" → "1.1"
2. `factors.factors` → `factors`
3. `signal.method`, `signal.entry_percentile`, `execution.order_style` 키 제거
4. `unary` 노드의 `rank/zscore/winsorize` → `kind: cross_sectional` 같은 operator,
   `unary: neutralize` → `cross_sectional: demean`
5. 나머지는 그대로. 기본값을 채우거나 지우지 않는다(생략은 사용자의 선택).

두 가지 입력 경로가 이 변환을 쓴다.

- **dict 경로**: repository codec이 저장된 `spec_json`을 읽을 때, 그리고 legacy_json revision의
  source 생성 시.
- **source 경로**: `POST /strategy-documents/upgrade`가 YAML/JSON 원문을 받아 주석·순서를 보존한
  1.1 원문을 돌려준다. YAML은 ruamel round-trip loader(`typ="rt"`)로 CST를 변환하고, JSON은
  들여쓰기를 유지해 재직렬화한다. 반환 전에 "변환된 원문을 parse한 tree == dict 경로로 변환한
  tree"를 검사해 두 구현의 drift를 fail-closed로 막는다. 응답에는 업그레이드된 source와 그 source의
  1.1 컴파일 결과(diagnostics, spec_hash)를 함께 담는다.

프론트는 업그레이드된 원문을 `setText` 한 번(undo 한 단계)으로 적용한다. 프론트가 변환 규칙을
알지 않는다.

### D4. 적용 불가 필드 경고의 owner

필드 적용 조건표는 `domain/strategy/_constraints.py`에 `FIELD_APPLICABILITY`로 둔다. 행은
`(pointer, conditions, description_key, owned_by_error)`이며 `conditions`는
`ApplicabilityCondition(pointer, equals | not_null)`의 AND 조합이다. 판정 predicate는 이 조건
데이터에서 파생되므로 선언(스키마 노출)과 판정(validator)이 갈라질 수 없다. 표는 다음과 같다
(P1-05 구현·Phase 1 감사로 8행 확정).

| pointer | 읽히는 조건 | owned_by_error |
|---|---|---|
| `/portfolio/selection_count` | `portfolio.selection_method == top_n` | — |
| `/portfolio/short_selection_count` | `portfolio.side == long_short` 그리고 `portfolio.selection_method == top_n` | — |
| `/portfolio/selection_percentile` | `portfolio.selection_method == percentile` | — |
| `/portfolio/rebalance_every_n_sessions` | `portfolio.rebalance == every_n_sessions` | — |
| `/portfolio/minimum_liquidity` | `portfolio.liquidity_field_id != null` | `strategy.portfolio.liquidity_field` |
| `/risk/sector_neutral` | `portfolio.side == long_short` | `strategy.risk.sector_neutral_side` |
| `/risk/risk_field_id` | `portfolio.weighting == risk` | — |
| `/signal/regime_minimum` | `signal.regime_field_id != null` | `strategy.signal.regime_field` |

validator(`validate_strategy(spec, written_pointers=...)`)는 parse tree에 그 pointer가 **명시적으로
존재**하고 값이 모델 기본값과 **다를** 때만 warning을 낸다. hydrate된 spec은 기본값과 명시값을
구분하지 못하므로 compile 서비스가 `key_ranges`의 pointer를 넘긴다. 기본값과 같은 명시값은
경고하지 않는다: canonical 문서(JSON 투영, legacy generated source, 포맷 변환 결과)는 모든 기본값을
적기 때문이다. severity는 warning이라 Save·Backtest를 막지 않는다.

`owned_by_error`가 있는 행은 이미 blocking error 규칙이 같은 관계를 소유한다. 그런 행은 runtime
schema와 contract에 조건을 노출하되 validator는 그 error 하나만 내고 warning을 두 번 내지 않는다.
runtime schema는 같은 표를 `x-applicable-when`(`all_of`·`description_key`·`owned_by_error`)으로,
contract는 `FieldContract.applicable_when`으로 같은 JSON 모양으로 노출해 Contract Inspector와 Form이
"현재 모드에서 읽히지 않음"을 표시한다. 코드 `strategy.field.inapplicable`는 `SEMANTIC_ONLY_CODES`에
등록한다.

### D5. Form·Graph 편집은 source 트랜잭션이다 (ADR D5 개정)

정본은 계속 YAML/JSON source 하나다. Form과 Graph는 별도 편집 모델을 갖지 않으며, 사용자의
GUI 동작을 **JSON Pointer 범위의 텍스트 교체**로 번역해 코드 편집기와 같은 `replaceRange`
트랜잭션을 실행한다. 이 방식은 ADR D5가 read-only를 택한 이유였던 "Form 전체 재직렬화가 주석과
순서를 잃는다"를 피한다.

```text
GUI 동작 (값 변경 / 필드 추가 / 항목 추가·삭제)
        │
        ▼
features/edit-strategy/model/source-transactions.ts
  parse.valueRanges / keyRanges로 대상 범위 계산
  yaml.stringify로 스칼라·fragment 직렬화
  들여쓰기·EOL 감지, preflight parseSource(nextSource) == ok
        │
        ▼
CodeEditorHandle.replaceRange(from, to, insert)   ← undo 한 단계
        │
        ▼
document reducer "edit" → parse → backend compile → spec/hash/diagnostics
```

편집 원시 연산은 네 가지다.

| 연산 | 대상 | 텍스트 변환 |
|---|---|---|
| `replaceScalar` | 존재하는 pointer | `valueRanges[pointer]` 범위를 새 스칼라 literal로 교체 |
| `insertKey` | 부모 mapping에 없는 키 | 부모 value 범위 끝(마지막 자식 줄 다음)에 `key: value` 줄 삽입. 부모가 `{}`이면 block mapping으로 교체 |
| `insertItem` | sequence | 마지막 항목 다음에 `- ...` fragment 삽입. `[]`이면 block sequence로 교체 |
| `remove` | 키 또는 항목 | 키 줄부터 그 value 범위 끝까지 삭제. 그 줄에 붙은 주석은 함께 삭제되고, 위 줄의 독립 주석은 남는다 |

불변식:

- 모든 연산은 `parseSource(nextSource).status === "ok"`를 preflight로 통과해야 적용된다.
  실패하면 아무것도 바꾸지 않고 사용자에게 이유를 보여준다.
- 연산 하나가 undo 한 단계다. Form 입력의 타이핑 중간값은 트랜잭션을 만들지 않는다(blur/commit 시).
- 기존 스니펫 삽입(P4-05)은 `insertKey`/`insertItem`의 특수 사례로 이 모듈 위에 다시 얹는다.
  스니펫 코드에 남아 있는 별도 fragment 로직은 제거한다.
- source가 syntax-invalid이면 Form과 Graph는 마지막 valid parse를 stale badge와 함께 보여주되
  편집은 잠근다. 편집 대상 범위를 신뢰할 수 없기 때문이다.
- JSON 문서는 v1에서 편집을 잠근다(스니펫과 같은 이유: 쉼표·brace 처리를 두 번째 구현으로 두지
  않는다). 안내 문구와 함께 YAML로 변환하는 기존 `포맷` 명령을 권한다.
- spec, spec_hash, semantic validation은 계속 backend compile만 믿는다. Form이 보여주는 값은
  parse tree(작성된 값)와 runtime schema의 default(생략된 값)이며, 컴파일 결과는 진단·badge에만
  쓴다.

### D6. Form 편집 모델

`features/edit-strategy/model/form-projection.ts`가 runtime schema와 parse tree를 결합해
필드 목록을 만든다. 프론트에 필드 이름, enum, 범위, 기본값을 손으로 적지 않는다(SoT 규칙).

- 섹션: `schema.properties`의 최상위 키 순서. `factors`, `eligibility.rules`, `parameters`는
  목록 섹션.
- 필드 한 줄: pointer, label(i18n key = pointer template), 현재 값(작성 여부 구분), 스키마
  type/enum/const/min/max/nullable/`x-catalog`/`x-reference`/`x-applicable-when`, 그 pointer의
  compile diagnostics.
- 컨트롤 선택은 스키마에서만 결정한다: enum → select, boolean → switch, integer/number →
  number input(min/max/step은 스키마 값), string+format date → date input, `x-catalog:
  equity-field|universe` → 기존 catalog query를 쓰는 picker, nullable → "설정 안 함" 옵션,
  `x-reference: node|parameter` → 문서 내 id select.
- 생략된 필드는 회색 placeholder로 기본값을 보이고, 값을 넣으면 `insertKey`. "기본값으로
  되돌리기"는 `remove`.
- 목록 섹션: 항목 추가는 스키마에서 materialize한 최소 항목(`canonical-snippets`의
  `materializeSchemaValue` 재사용)을 `insertItem`, 삭제는 `remove`. 팩터 카탈로그 preset은 기존
  스니펫 카탈로그를 "항목 추가" 메뉴로 노출한다. 파라미터는 `kind` 선택 후 분기 스키마로 필드를
  만든다.
- 팩터 항목의 `graph`는 Form에서 편집하지 않는다. "Graph에서 열기"로 D7에 넘긴다.
- IME: `composing` 중에는 트랜잭션을 만들지 않는다(스니펫과 같은 게이트).

### D7. Graph 편집 모델

`features/edit-strategy/model/graph-transactions.ts`가 팩터 하나의 `graph` pointer 아래 편집을
D5 원시 연산으로 번역한다. 기존 DAG projection(backend execution plan 순서, 입력 연결, 진단)은
유지하고 편집 컨트롤을 더한다.

- **노드 추가**: `kind` 선택 → 스키마 분기(`$defs/<Kind>Node`)로 필드를 materialize → `nodes`에
  `insertItem`. `node_id`는 `<operator>_<n>` 형식으로 문서 안에서 유일하게 제안하며 사용자가 바꿀
  수 있다.
- **노드 필드 편집**: 선택한 노드의 property editor. `x-reference: node` 필드는 같은 그래프의 노드
  id select(자기 자신 제외). `operator`는 kind별 enum. 값 변경은 `replaceScalar`.
- **입력 재연결**: DAG 화면에서 입력 슬롯을 클릭해 대상 노드를 고른다. 내부적으로 해당
  `*_node_id` 필드의 `replaceScalar`.
- **출력 노드 지정**: `output_node_id` `replaceScalar`.
- **노드 삭제**: 다른 노드나 `output_node_id`가 참조하면 참조 목록을 보여주고 거부한다.
  참조가 없을 때만 `remove`.
- **`missing_policy`**: enum select.
- 사이클·타입 불일치·미사용 노드 판정은 계속 backend validation과 plan 진단이 한다. 프론트는
  "참조 존재"만 문서 tree에서 확인한다(삭제 가드에 필요한 최소 사실).
- 좌표·배치는 계속 frontend local UI state다(SoT 표: 미저장 편집 상태·그래프 좌표).

### D8. 상태 소유권

| 상태 | 소유자 |
|---|---|
| YAML/JSON source, undo history, parse 상태 | `features/edit-strategy` (변경 없음) |
| Form/Graph가 보여주는 값 | parse tree + runtime schema default. 서버 spec은 진단·badge에만 |
| Form 입력 중 커밋 전 텍스트, 선택 노드, 펼침 상태 | feature local UI state |
| 필드 목록·enum·범위·기본값·적용 조건 | backend runtime schema |
| spec·spec_hash·semantic/structural 진단 | backend compile |
| 1.0 → 1.1 변환 규칙 | `domain/strategy/_upgrade.py` |
| 적용 불가 필드 조건표 | `domain/strategy/_constraints.py` |

### D9. 규칙·문서 갱신

| 위치 | 변경 |
|---|---|
| `.claude/rules/strategy-workbench-sot.md` | "전략 의미" 행을 "Form/Graph 편집은 source 트랜잭션, 별도 편집 모델 없음"으로. `x-applicable-when`과 업그레이드 변환 owner 행 추가 |
| `.claude/rules/frontend-testing.md` | round-trip 대상에 "GUI 트랜잭션 → source → spec" property test 추가 |
| ADR 2026-09-04 authoring contract | 머리말에 이 문서로의 개정 링크. D2 예시를 1.1로, D5를 개정 표기 |
| `docs/planning/strategy-workbench-yaml-ui/WORKFLOW.md` 2.2 | 예시를 1.1로 |
| `backend/tests/fixtures/strategy_documents/*` | 1.1 fixture. `quality_momentum.v1_0.yaml`을 업그레이드 golden으로 추가 |
| `docs/manual/strategy-workbench/README.md`, `README.md`, `frontend/README.md`, `backend/FACTORS.md` | 1.1 문법과 GUI 편집 설명 |
| 로드맵 M8 | "Graph 직접 편집"을 이 initiative가 제공한다고 링크 |

## 3. Non-goals

- 표현식 문자열 DSL, 단위 literal(`5%`, `15bps`), YAML anchor/alias
- `portfolio`의 모드별 필드를 discriminated union으로 재구조화(1.2 후보. 1.1은 flat 유지 + 경고)
- JSON 문서의 GUI 편집
- Graph 캔버스의 자유 배치·드래그 배선(v1은 슬롯 선택 UI)
- 다중 사용자 동시 편집, 자동 merge
- 1.0 revision의 in-place 재해시나 DB 마이그레이션. 1.0 row는 동결 이력이다
- Rust engine 변경. 엔진은 compile된 target tape를 받으며 authoring 모양을 모른다

## 4. PR 스택 개요

각 PR은 WORKFLOW 12절 크기 규칙(150–450 lines, 파일 8개 이내)과 13절 절차(self-check → diff
freeze → Opus reviewer 1명 → 재검토 → APPROVE)를 따른다. 스택은 `main` 위에 순서대로 쌓고 PR
base는 직전 PR 브랜치다. 상세 acceptance는 PLAN.md가 소유한다.

| Phase | PR | 내용 |
|---|---|---|
| A. schema 1.1 backend | A-01 | 모델 1.1: `factors` 평탄화, 선택 섹션·`label`·`weight`·`market` 기본값, `kw_only` kind-first. hydrate·canonical·schema·validation·compiler·trace·explanation 경로 갱신, fixture 1.1, hash golden |
| | A-02 | 미사용 필드·enum 제거, unary alias 제거와 `cross_sectional: demean`, constraint catalog·i18n key 정리 |
| | A-03 | `_upgrade.py` dict 변환 + repository codec 1.0 동결 읽기 + saved-reference backtest 거부 |
| | A-04 | `POST /strategy-documents/upgrade` (ruamel rt source 변환, drift 검사), OpenAPI·SDK |
| | A-05 | `FIELD_APPLICABILITY` + compile warning + `x-applicable-when` |
| B. frontend 1.1 | B-01 | generated types·pointer 헬퍼·snippet·outline·execution plan·graph·debugger를 1.1 pointer로, e2e fixture 1.1 |
| | B-02 | 1.0 문서 업그레이드 배너·동작, Contract Inspector 적용 조건 표시 |
| C. source 트랜잭션 | C-01 | `source-transactions.ts` 원시 연산 4종 + preflight + property test |
| | C-02 | `useSourceTransactions` 훅, 스니펫 삽입을 이 위에 재구성 |
| D. Form 편집 | D-01 | `form-projection.ts` (스키마 × tree × 진단 → 필드 목록) |
| | D-02 | 스칼라·enum·boolean·date·nullable·catalog·reference 컨트롤과 트랜잭션 연결 |
| | D-03 | 목록 섹션: eligibility rules, parameters(kind 분기), factors 헤더 + preset 추가 |
| | D-04 | IDE·page 연결, stale/invalid/JSON 잠금, i18n, e2e |
| E. Graph 편집 | E-01 | `graph-transactions.ts`: 노드 추가·필드 편집·재연결·출력 지정·삭제 가드 |
| | E-02 | Graph UI: 노드 추가 메뉴, property editor, 입력 슬롯 선택, 키보드·ARIA |
| | E-03 | 연결·e2e·문서·규칙 마감(ADR/SoT 개정 최종 반영, 로드맵 M8) |

## 5. 테스트

- **backend**: 1.1 fixture 3종(yaml/json/legacy) 같은 hash golden. `quality_momentum.v1_0.yaml`
  → `upgrade_document_1_0` → 1.1 fixture와 tree 동일. ruamel 변환 결과의 주석·순서 보존 golden.
  1.0 row 동결 읽기(정상/변조 4종 fail-closed). saved-reference backtest 422. 적용 불가 경고는
  명시값에만 발생하고 생략값에는 없음. 스키마 property 순서 `kind` 우선.
- **frontend 단위**: 트랜잭션 4종 property test — 임의 1.1 문서와 임의 연산에 대해
  `parse(apply(op, source)).tree == applyToTree(op, parse(source).tree)`이고 무관한 주석·줄이
  바이트 그대로 남는다. Form 컨트롤은 스키마 fixture에서만 유도되며 MSW로 compile 왕복을 검증한다.
  Graph 삭제 가드, id 제안 유일성, 참조 select 자기 제외.
- **e2e**: 1.0 revision 열기 → 업그레이드 → 저장 → backtest. Form에서 값 변경 → YAML 탭에서
  주석 유지 확인 → 저장 hash가 YAML 직접 편집과 동일. Graph에서 노드 추가·재연결 → plan 갱신.

## 6. 롤백

- A 단계는 backend만 바꾼다. revert하면 1.0 모델로 돌아가며 그 사이 저장된 1.1 revision은 읽을
  수 없으므로, A 단계 merge 전에는 실 DB에 1.1 문서를 저장하지 않는다(로컬 fixture DB로 검증).
- C~E는 frontend feature 모듈 추가이므로 PR 단위 revert가 가능하다. Form/Graph 편집 컨트롤을
  숨기면 read-only projection으로 돌아간다.

## 7. 대안

- **1.0을 계속 지원하며 hash를 유지**: 1.1 모델에서 1.0 hash를 재현하려면 제거한 필드를 모델에
  남겨야 해 "미사용 필드 제거"가 불가능하다. 동결 이력 + 새 revision 방식을 택했다.
- **DB 마이그레이션으로 1.0 row 재작성**: immutable trigger와 run manifest provenance를 깨고,
  과거 backtest가 참조한 hash가 사라진다. 거부.
- **Form 전체 재직렬화**: ADR D5가 거부한 이유가 그대로 유효하다.
- **모드별 필드 discriminated union(1.1에 포함)**: 모델·컴파일러·UI가 한꺼번에 바뀌고 GUI 착수가
  밀린다. 1.1은 경고로 안전성만 확보하고 구조 변경은 1.2 후보로 남긴다.
