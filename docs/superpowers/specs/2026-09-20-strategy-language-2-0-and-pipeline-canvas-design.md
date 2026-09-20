# 설계: 전략 언어 2.0과 파이프라인 캔버스

> 작성: 2026-09-20
>
> 상태: Accepted (제품 소유자 결정 2026-09-20: "템플릿이 주가 되면 안 된다. 범용적이고 다양한 전략
> 구성이 목표다. 언어 밖으로 표현할 수 있는 것(데이터베이스·유니버스 종류 등)은 언어에서 뺀다")
>
> 선행 계약: [schema 1.1·GUI 편집 설계](./2026-09-17-strategy-schema-1-1-and-gui-editing-design.md)
> (D1 문법, D2 동결 이력, D3 업그레이더, D5 source 트랜잭션을 이 문서가 개정·확장한다),
> [Strategy Authoring Contract ADR](./2026-09-04-strategy-authoring-contract-adr.md)
> (D2 verbose 문법, D8 대상 사용자를 이 문서가 개정한다)
>
> 원인 분석과 화면 시안: 디자인보드 <https://claude.ai/code/artifact/4f3d7f4d-95e2-4ad4-a29e-37c2ad49186d>
>
> PR 진행은 [docs/planning/strategy-language-2-0/PLAN.md](../../planning/strategy-language-2-0/PLAN.md)

## 1. 맥락

schema 1.1과 Form/Graph 편집(2026-09-17 initiative, 19 PR)이 끝난 뒤에도 "비전공자가 그래프만
만져서 전략을 만든다"는 목표는 닿지 않았다. 2026-09-20 감사(frontend·backend 코드 감사 2건,
퀀트 아이디어 5개를 1.1 YAML로 작성해 hydrate·validate 실행)로 확인한 원인은 문법이 아니라
개념 층위다.

| 관찰 | 근거 |
|---|---|
| 화면 어휘가 스키마 식별자 그대로다. 노드 kind 12, 연산자 18, 참조 필드 11이 드롭다운·라벨에 노출되고, 설명이 붙은 property는 121개 중 13개, 그래프 노드는 0개 | `_schema.py:245`가 title에 파이썬 클래스명, `strategy-form-panel.tsx:641`이 `<code>{field.key}</code>` |
| Graph 캔버스는 카드를 한 줄로 흘리는 CSS 그리드, 엣지는 글자 `→`, 연결은 `input_node_id` 드롭다운. 좌표·드래그·되돌리기 버튼·이동·복제 연산이 없다 | `factor-graph-panel.css:108-111`, `source-transactions.ts:29-45` |
| 합성 점수가 `Σ(방향×weight×원시값)/Σ|weight|`라 단위가 다른 팩터를 섞으면 큰 단위가 지배한다. 검증은 이슈 0건 | `portfolio/_compiler.py:526-570` |
| 저장 게이트가 실행 게이트보다 느슨하다. `field_id` 오타, boolean 출력, `saved_factor`가 저장까지 통과하고 백테스트에서 422 | `strategy/_validation.py:339-343`, `portfolio_design/_service.py:452, 660-685` |
| `group` 노드는 duckdb 어댑터가 모든 필드를 `NUMERIC_SERIES`로 고정 반환해 실데이터에서 절대 성공하지 못하는데 메뉴에 그대로 있다 | `equity_duckdb/_adapter.py:734-747` |
| 문제 목록과 "검증 통과" 배지는 YAML 탭 안에만 렌더된다. e2e도 그래프 편집 뒤 YAML 탭으로 돌아가 확인한다 | `strategy-ide.tsx:614-631`, `workbench.workflow.spec.ts:1098` |
| 실행 설정(`BacktestRunSpec`)에 엔진·초기 자본·벤치마크·연환산·OOS 창이 이미 있고 기간만 전략 문서 `data.start/end`에서 읽어 온다 | `run-settings.ts:3-6`, `backtest_run/_service.py:436-437` |
| 새 전략은 두 줄 YAML과 구조 오류 2개(`data`·`factors` 필수)로 시작한다 | `new-strategy-page.tsx:56`, `_models.py:193-205` |

1.1이 줄인 것은 타이핑 분량이고, Graph 쓰기가 바꾼 것은 편집 가능 여부다. 사용자가 이름을 짓고,
잇고, 출력을 지정하고, 실행 환경을 전략 문서에 적어야 하는 구조는 그대로다.

## 2. 결정

### D1. 대상 사용자와 완료 정의를 되돌린다 (ADR D8 개정)

ADR D8은 완료 정의를 "전략 정의는 문서로 작성하되 … 대상 사용자는 전문 트레이더다"로 낮췄다.
이 initiative는 로드맵의 원래 문장 **"코드를 몰라도 대부분의 cross-sectional 전략을 만들 수
있다"**를 완료 정의로 되돌린다. 두 사용자를 층으로 나눈다.

| 층 | 사용자 | 화면 | 노출되는 식별자 |
|---|---|---|---|
| 파이프라인·팩터 | 비전공자 | 4단계 캔버스, 레시피 빌더 | 없음 |
| 고급 | 전문가 | 노드 캔버스 | 접힌 영역에서만 |
| 원문 | 전문가·자동화 | YAML 편집기 | 전부 |

완료 정의의 측정은 감사에 쓴 **퀀트 아이디어 5개**로 고정한다: 12-1 모멘텀, 저PBR+고ROE 결합,
20일 이평 돌파, 거래대금 상위 20% 필터, 변동성 역가중. 다섯 개 모두 파이프라인·팩터 층에서만
만들어 백테스트까지 도달해야 한다.

템플릿·프리셋은 주 진입 경로가 아니다. 예시 전략은 튜토리얼 문서(매뉴얼)에만 둔다.

### D2. 전략 언어에는 전략 논리만 남긴다 (schema 2.0)

언어 밖에서 정할 수 있는 것은 실행 설정으로 옮기고, 기계가 채울 수 있는 것은 없앤다.

| 항목 | 2.0에서 | 이유 |
|---|---|---|
| `data.market`, `data.frequency` | 실행 설정 | 선택지가 하나뿐이고 시장은 데이터 연결의 속성이다 |
| `data.start`, `data.end` | 실행 설정 | 같은 전략을 다른 기간에 돌리는 것이 run의 본질. 실행 설정에 이미 OOS 창·연환산이 있다 |
| `data.universe_id` | 실행 설정 | 전략은 "그 안에서 어떻게 거를지"(filter)만 안다 |
| `execution.*` (timing, participation_rate, fee_bps, slippage_bps) | 실행 설정 | 초기 자본·벤치마크·엔진과 같은 층 |
| `eligibility.rules` + `portfolio.liquidity_field_id/minimum_liquidity` | `filter` | 같은 개념(점수 전 제외)이 두 곳에 있었다 |
| `signal.*` + 결합 방식 | `combine` | 점수를 합치는 단계 |
| `portfolio`의 선택·리밸런스 필드 | `select` | method별 discriminated union |
| `portfolio.weighting` + `risk.*` | `weight` | method별 discriminated union |
| 노드 `kind` | steps 표기에서 삭제 | 18개 연산자 중 두 kind에 걸치는 것이 없다(`_nodes.py:20-51`) |
| `node_id`, `input_node_id`, `output_node_id` | steps 표기에서 삭제 | 순서가 배선, 마지막이 출력 |
| 팩터 `label` | 삭제 | `id`가 표시 이름. 한글 이름은 `title`처럼 `id`에 그대로 쓴다 |
| `graph.missing_policy` | 실행 설정 기본값 + 팩터별 선택(`missing`) | 지식이 아니라 데이터(제외 종목 수)로 결정하게 한다 |

2.0 문서의 모양은 다음과 같다. 같은 전략을 1.1로 적으면 29줄에 배관 9줄이고, 2.0은 21줄에 배관
0줄이다.

```yaml
schema_version: "2.0"
title: 가치 + 모멘텀
filter:
  - field: price.trading_value
    min: 1000000000
factors:
  - id: momentum
    direction: high
    weight: 0.6
    steps:
      - field: price.close
      - momentum: { window: 252, lag: 21 }
      - rank
  - id: value
    direction: high
    weight: 0.4
    steps:
      - divide: [financial.book_equity, price.market_cap]
      - rank
combine: { method: rank_weighted }
select: { method: top_n, count: 20, rebalance: monthly }
weight: { method: equal, max_per_name: 0.05 }
```

- 최상위 키는 `schema_version`, `title`, `description`, `filter`, `factors`, `combine`, `select`,
  `weight`, `parameters` 아홉 개다. 필수는 `schema_version`, `title`, `factors` 셋이다. 빈 문서에
  `title`만 있어도 `factors: []`는 structural error가 아니라 semantic error(`strategy.factor.required`)다.
  그래서 새 전략의 시작 문서가 "구조 오류"가 아니라 "팩터를 추가하세요"로 시작한다.
- canonical payload는 계속 모든 기본값을 채우고, `spec_hash`는 canonical의 sha256이다(ADR D3 불변).
  **hash는 이제 전략 논리만 덮는다.** 같은 전략을 다른 기간·유니버스·수수료로 돌려도 revision이
  늘지 않는다.
- `CURRENT_SCHEMA_VERSION = "2.0"`, `SUPPORTED_SCHEMA_VERSIONS = ("2.0",)`. 1.0·1.1 문서는
  D8의 업그레이드 경로로만 들어온다.

### D3. 팩터의 기본 표기는 `steps`다. `nodes`는 고급 표기로 남는다

`steps`는 위에서 아래로 계산하는 순서 목록이다. 문자열 DSL이 아니라 YAML 구조이므로 parser·source
map·pretty-printer가 필요 없고, 레시피 빌더의 단계 목록과 1:1이다.

- 단계는 `- <연산자>: <파라미터>` 매핑 하나이거나, 파라미터가 없으면 `- <연산자>` 문자열이다.
- 첫 단계는 소스여야 한다: `field: <field_id>`, `constant: <number>`, `parameter: <parameter_id>`.
  단, 다중 입력 연산자(`add`·`subtract`·`multiply`·`divide`·`gt`·`gte`·`lt`·`lte`·`eq`)가 입력
  배열을 모두 명시하면 첫 단계일 수 있다.
- 단일 입력 연산자(`negate`·`lag`·`mean`·`std`·`momentum`·`delta`·`min`·`max`·`rank`·`zscore`·
  `winsorize`·`demean`·`neutralize`·`group_rank`)는 직전 단계의 출력을 입력으로 받는다.
- 다중 입력 연산자는 `inputs` 배열을 받는다. 배열 원소는 문자열 `prev`(직전 단계 출력), field id
  문자열, 숫자(상수), `{ parameter: id }`, `{ steps: [...] }`(중첩 체인) 중 하나다. `- divide: [a, b]`는
  `- divide: { inputs: [a, b] }`의 축약이다. `- subtract: [prev, { steps: [...] }]`로 이격도를 적는다.
- `conditional`(참이면 A 아니면 B)은 `- when: { if: <input>, then: <input>, else: <input> }`이다.
  `if`의 입력이 boolean이 아니면 semantic error.
- 마지막 단계의 출력이 팩터 출력이다. 출력이 boolean이면 D5의 승격 규칙이 0/1로 바꾼다.
- hydrate가 `steps`를 `FactorGraph`로 컴파일한다. node_id는 `s<단계 순번>`(중첩은 `s3.1`)로
  결정적으로 생성되고, `output_node_id`는 마지막 단계다. **같은 그래프를 `steps`로 적든 `nodes`로
  적든 canonical payload와 `spec_hash`가 같다.** canonical의 팩터 표기는 계속 `nodes`다(JSON 투영,
  legacy generated source, 실행 plan이 읽는 모양). authoring source만 `steps`를 허용한다.
- `nodes` 표기는 1.1 그대로(`kind`, `node_id`, `*_node_id`) 한 팩터 안에서 계속 쓸 수 있다. 한 팩터가
  `steps`와 `nodes`를 동시에 가지면 structural error. `saved_factor`·`saved_subgraph`는 실행 경로가
  없으므로 2.0 스키마에서 뺀다(로드맵 M8 "reusable factor library"가 되살릴 때 2.x로 추가).
- `POST /api/v1/strategy-documents/expand-steps`가 `steps` 원문을 같은 의미의 `nodes` 원문으로
  바꿔 돌려준다(D8의 업그레이드 엔드포인트와 같은 ruamel round-trip·drift 검사 방식). 레시피
  빌더의 "고급으로 보기"가 이 응답을 편집기 범위 교체 한 번으로 적용한다. 역방향(`nodes` → `steps`)은
  단일 입력 체인일 때만 가능하며 v1 non-goal이다.

### D4. `combine`이 정규화를 소유한다

```yaml
combine:
  method: rank_weighted   # rank_weighted | zscore_weighted | raw_weighted
  score_threshold: null
  regime: null            # { field: <field_id>, min: <number> }
```

- `composite = Σ(sign(direction) × weight × norm(x)) / Σ|weight|`. `norm`은 method가 정한다:
  `rank_weighted`는 횡단면 0~1 순위, `zscore_weighted`는 횡단면 표준화, `raw_weighted`는 항등(1.1의
  의미).
- 기본값은 `rank_weighted`다. 두 팩터의 출력 단위(`NodeContract.unit`)가 다른데 `raw_weighted`이면
  `strategy.combine.unit_mismatch` warning을 낸다. 팩터가 하나면 method는 순위를 바꾸지 않으므로
  경고하지 않는다.
- 1.1에서 올라온 문서는 업그레이더가 `combine.method: raw_weighted`를 명시해 실행 의미를 보존한다.

### D5. 저장 게이트와 실행 게이트를 하나로 합친다

`POST /strategy-documents/compile`이 실행 직전 검사와 같은 규칙을 적용한다.

- compile 서비스가 연결된 equity 어댑터의 필드 메타데이터를 `validate_strategy`에 넘긴다.
  `factor.graph.field_missing`(field_id 오타)이 저장 전에 난다. 어댑터가 없는 컨텍스트(CLI·테스트)는
  `require_field_metadata=False`로 지금과 같다.
- 팩터 출력 타입 검사가 compile로 앞당겨진다. boolean 출력은 팩터 경계에서 0/1 `numeric_series`로
  승격한다(D3). scalar 출력은 `strategy.factor.output_type` error. `_reject_non_numeric_factor_outputs`는
  남기되 여기서 새 오류가 나면 결함이다(회귀 테스트).
- 실데이터 어댑터가 `GROUP_SERIES` 필드를 제공하기 전까지 `neutralize`·`group_rank` 단계는
  연산자 카탈로그에서 `availability: unsupported`로 표시되고 레시피 팔레트에 나오지 않는다. 문서에
  쓰면 compile이 `strategy.operator.unsupported` error를 낸다(어댑터 capability 조회).
- 실행 차단 판정은 계속 backend compile diagnostics의 error severity 하나다(SoT 규칙 불변).

### D6. `filter`·`select`·`weight`의 모양

```yaml
filter:                                  # 순서 없음, 전부 AND
  - field: price.trading_value            # 데이터 필드 또는
    min: 1000000000                       #   min / max / top_percent / top_count 중 하나 이상
  - steps: [ { field: price.trading_value }, { mean: { window: 20 } } ]
    top_percent: 0.5                      # 계산값으로 거르기(횡단면 상위 50%)
select:
  method: top_n                           # top_n | percentile
  count: 20                               # top_n
  percentile: 0.1                         # percentile
  side: long_only                         # long_only | long_short
  short_count: 20                         # long_short + top_n
  rebalance: monthly                      # weekly | monthly | quarterly | { every_sessions: 21 }
  turnover_buffer: 0
  minimum_trade_weight: 0.0
weight:
  method: equal                           # equal | factor_score | rank | inverse
  by: null                                # inverse: 팩터 id 또는 field_id (x-reference: factor | equity-field)
  max_per_name: 0.1
  max_per_sector: 0.3
  gross_exposure: 1.0
  net_exposure: 1.0
  sector_neutral: false
```

- `select`·`weight`는 `method`를 판별자로 하는 union이다. runtime schema는 `oneOf` + `x-discriminator`로
  내보내고 Form은 이미 `parameters[].kind`에서 쓰는 분기 방식을 재사용한다. `FIELD_APPLICABILITY`
  8행 중 6행이 union으로 흡수되어 사라진다(`strategy.field.inapplicable`는 남는 2행에만).
- `weight.method: inverse`의 `by`가 팩터 id를 받으면 그 팩터의 출력으로 역가중한다. 1.1의
  `weighting: risk` + `risk_field_id`는 `inverse` + `by: <field_id>`로 올라간다.
- `filter`의 `steps`는 D3와 같은 문법이다. 필터에 쓴 계산은 점수에 들어가지 않는다.

### D7. 실행 설정(`RunEnvironment`)

```python
@dataclass(frozen=True, kw_only=True)
class RunEnvironment:
    market: Market = Market.KRX
    frequency: DataFrequency = DataFrequency.DAILY
    start: date
    end: date
    universe_id: str
    timing: ExecutionTiming = ExecutionTiming.NEXT_OPEN
    participation_rate: float = 0.1
    fee_bps: float = 15.0
    slippage_bps: float = 10.0
    missing: MissingPolicy = MissingPolicy.DROP
```

- owner는 `domain/backtest/_models.py`다(전략이 아니라 실행의 사실). `BacktestRunSpec`,
  portfolio preview 요청, trace 요청이 `environment` 필드로 받는다. 세 요청이 같은 타입을 쓴다.
- run manifest는 `spec_hash`와 별개로 `environment_hash`(canonical JSON sha256)를 기록한다. 캐시
  키에도 들어간다.
- frontend 실행 설정 패널이 필드를 소유한다. 기본값은 runtime schema `RunEnvironment` default에서
  오고, 마지막 사용값은 전략별 local UI state(`localStorage`)다. 서버는 run manifest에만 기록한다.
  전략 revision에 실행 설정을 저장하지 않는다(그것이 hash에서 뺀 이유다).
- 전환 규칙: P2-01은 `environment`를 optional로 받고 없으면 `spec.data`·`spec.execution`에서
  채운다(브리지). P2-02가 `data`·`execution`을 모델에서 지우면 `environment`가 필수가 된다.

### D8. 1.1 → 2.0 업그레이드와 동결 이력

- `domain/strategy/_upgrade.py`의 `UPGRADE_STEPS`에 1.1 → 2.0 step을 잇는다. 1.0 문서는 1.0 → 1.1 →
  2.0을 순서대로 거친다. dict 경로와 source 경로(ruamel round-trip)가 같은 step을 쓰는 구조는
  그대로다.
- 변환: `schema_version` → `"2.0"`; `data`·`execution` 제거 후 **응답의 `environment`로 반환**;
  `eligibility.rules` + `portfolio.liquidity_*` → `filter`; `signal` → `combine`(method는
  `raw_weighted` 명시); `portfolio` 선택 필드 → `select`(method별 union); `portfolio.weighting` +
  `risk` → `weight`; 팩터 `label` 제거(값이 `factor_id`와 다르면 `id`를 `label`로, 기존 `factor_id`는
  주석으로 남긴다); `graph.missing_policy` → 팩터 `missing`; `graph.nodes`는 그대로(`steps`로
  바꾸지 않는다. 역변환은 non-goal).
- `POST /strategy-documents/upgrade` 응답에 `environment`가 추가된다. frontend는 원문을 편집기 범위
  교체 한 번으로 적용하고, `environment`로 실행 설정 패널을 채운다.
- 1.1 revision은 1.0과 같은 동결 이력이다. `is_frozen_schema_version`은 이미 `!= CURRENT`라 코드
  변경 없이 1.1 row가 동결된다. saved-reference backtest는 `422 strategy_revision_requires_upgrade`.
- 1.1 → 2.0 업그레이드 golden: `quality_momentum.v1_1.yaml`(현재 fixture 보존) → 2.0 fixture와 tree
  동일, 주석·순서 보존.

### D9. 연산자 카탈로그 (backend owner)

팩터에는 `FactorDefinition.description`이 있지만 연산자에는 없다. `domain/factor/_operators.py`에
`OperatorDefinition(operator, kind, arity, params, output_type_rule, unit_rule, availability,
description_key, formula_key, example)`을 두고 `GET /api/v1/strategy-documents/operators`와 runtime
schema(`x-description-key`, `x-operator`)로 내려준다. 문장은 frontend i18n(`operator.<name>.*`)이
렌더한다(적용 조건 문장과 같은 소유 규칙: 키는 backend, 문장은 소비자별).

레시피 팔레트, 고급 캔버스 팔레트, Contract Inspector가 같은 카탈로그를 읽는다. frontend에 연산자
목록·설명을 손으로 적지 않는다.

### D10. 새 화면 층은 전부 source 트랜잭션 위에 얹힌다 (1.1 spec D5 유지)

| 화면 | 투영 입력 | 편집 |
|---|---|---|
| 파이프라인 캔버스 | runtime schema × parse tree × compile 진단 → 4단계 모델(`pipeline-projection.ts`) | 단계 카드 컨트롤은 Form 필드 컨트롤 재사용(`replaceScalar`·`insertKey`·`remove`). 팩터 추가는 빈 `steps` 항목 `insertItem` |
| 레시피 빌더 | 팩터 하나의 `steps` pointer 아래(`recipe-projection.ts`) | 단계 추가·삭제·순서 변경·파라미터는 `recipe-transactions.ts`가 `steps[]` 항목 연산으로 번역. 순서 변경은 `remove` + `insertItem`을 `planSourceOperations`로 한 undo 단계에 |
| 고급 노드 캔버스 | 팩터 하나의 `nodes` pointer 아래 + backend plan | 1.1 `graph-transactions.ts` 유지. 드래그 배선은 `*_node_id` `replaceScalar`. 좌표는 local UI state |
| 실행 설정 띠 | `RunEnvironment` runtime schema | 문서 밖. `run-settings.ts` 필드 |
| 기준일 미리보기 | 기존 trace API(`debug-strategy`) | 읽기 전용 |

- 파이프라인 탭이 기본 탭이 된다. 탭 순서: 파이프라인 · 팩터 · 고급(노드 그래프) · YAML 원문 · 이력.
- 문제 목록과 "검증 통과" 배지는 탭과 무관하게 렌더된다(P1-01). 진단은 pointer로 해당 카드에
  붙는다.
- 되돌리기·다시 실행은 툴바 버튼과 전역 단축키다. 편집기가 hidden이어도 동작한다(P1-02).
- 파이프라인·팩터 탭에는 YAML 식별자가 나오지 않는다. 확인 방법: 그 두 탭의 DOM 텍스트에
  `_id`·`_node`·`kind:` 패턴이 없다는 e2e 단언.

### D11. 상태 소유권 (1.1 spec D8 갱신)

| 상태 | 소유자 |
|---|---|
| 전략 논리(spec, spec_hash) | backend compile, `StrategySpec` 2.0 |
| 실행 설정 | `RunEnvironment`(domain/backtest). 편집 중 값은 frontend 실행 설정 패널, 실행된 값은 run manifest |
| `steps` → `FactorGraph` 컴파일 규칙, node_id 생성 규칙 | `domain/strategy/_hydrate.py`(steps 컴파일러) |
| 1.0 → 1.1 → 2.0 변환 | `domain/strategy/_upgrade.py` `UPGRADE_STEPS` |
| steps → nodes 원문 변환 | `adapters/outbound/document_codec/_expand_steps.py` (D3, D8과 같은 drift 검사) |
| 연산자 정의·설명 키·가용성 | `domain/factor/_operators.py` |
| 정규화 방식과 합성 공식 | `domain/portfolio/_compiler.py`가 `combine`을 읽는다 |
| 4단계·레시피 투영 | `features/edit-strategy/model/pipeline-projection.ts`, `recipe-projection.ts` |
| 그래프 좌표·펼침·선택 | frontend local UI state |

### D12. 규칙·문서 갱신

| 위치 | 변경 |
|---|---|
| `.claude/rules/strategy-workbench-sot.md` | "전략 의미" 행에 2.0·steps·파이프라인/레시피 투영 추가, "실행 설정" 행 신설, "연산자 정의" 행 신설, 업그레이드 행 1.1 → 2.0, 금지 절의 "표현식 문자열 DSL" 유지(steps는 구조) |
| ADR 2026-09-04 | D2 예시 2.0, D8 대상 사용자·완료 정의 개정 링크 |
| 1.1 spec | 머리말에 이 문서로의 개정 링크 |
| 로드맵 | 완료 정의 원문 복귀, M8 "custom formula editor" 문구를 "steps는 2.0이 제공, 문자열 DSL은 여전히 non-goal"로 |
| `docs/manual/strategy-workbench/README.md`, `README.md`, `frontend/README.md`, `backend/FACTORS.md` | 2.0 문법, 파이프라인·레시피 화면, 예시 전략 5개(튜토리얼) |
| fixture | `quality_momentum.yaml` 2.0(steps), `.nodes.yaml`(같은 hash), `.v1_1.yaml`(업그레이드 golden), `ideas/*.yaml` 5개(완료 정의) |

## 3. Non-goals

- 표현식 문자열 DSL, 단위 literal(`5%`, `15bps`), YAML anchor/alias
- `nodes` → `steps` 역변환
- `saved_factor`·`saved_subgraph`·재사용 팩터 라이브러리(M8)
- 파라미터 탐색 UI(M6). 2.0은 `parameter` 참조를 `steps`의 숫자 자리에서 허용하는 데까지만
- KRX 외 시장, 일별 외 빈도(실행 설정 enum은 하나뿐이어도 남긴다)
- 다중 사용자 동시 편집, 자동 merge
- 1.0·1.1 revision의 in-place 재해시나 DB 마이그레이션
- Rust engine 변경. 엔진은 compile된 target tape를 받는다
- 자연어 → 전략 생성(LLM). 파이프라인 캔버스가 자리 잡은 뒤 별도 initiative

## 4. Phase와 PR 스택 개요

Phase 1은 규칙 변경 없이 1.1 위에서 끝나며 2.0 뒤에도 그대로 남는다. Phase 2~3이 언어, Phase 4~6이
새 화면 층이다. 상세 acceptance는 WORKFLOW.md, 상태는 PLAN.md가 소유한다.

| Phase | 목표 | PR |
|---|---|---|
| P0 | 기획 패키지·계약 문서 개정 | P0-01 |
| P1 | 화면 안에서 끝나는 마찰 제거(진단·되돌리기·한글 어휘·연산자 카탈로그·조용한 실패) | P1-01 ~ P1-05 |
| P2 | backend schema 2.0(실행 설정 분리, 섹션 재구성, steps, combine, 단일 게이트, 업그레이더) | P2-01 ~ P2-07 |
| P3 | frontend 2.0 적응(SDK·투영·실행 설정 패널·업그레이드·e2e·매뉴얼) | P3-01 ~ P3-03 |
| P4 | 파이프라인 캔버스(4단계 카드, 팩터 카드, 미리보기, 기본 탭, 빈 화면 e2e) | P4-01 ~ P4-04 |
| P5 | 레시피 빌더(steps 편집, 팔레트, 결과 미리보기, 고급 전환, 아이디어 5개 e2e) | P5-01 ~ P5-04 |
| P6 | 고급 노드 캔버스(라이브러리 ADR, 드래그 배선, 노드 위 진단, 마감) | P6-01 ~ P6-03 |

## 5. 완료 정의(initiative)

1. 감사의 아이디어 5개가 각각 파이프라인·팩터 탭만으로 빈 문서에서 만들어져 백테스트 실행까지
   도달한다(e2e 5건, `fixtures/strategy_documents/ideas/*.yaml`이 그 결과 원문과 같은 hash).
2. 파이프라인·팩터 탭 DOM에 YAML 식별자가 없다(e2e 단언).
3. compile "검증 통과" 문서가 preview·backtest에서 422가 나지 않는다(fixture 전수 + property).
4. 같은 그래프의 `steps`·`nodes` 문서가 같은 `spec_hash`를 낸다.
5. 1.1 fixture 전부가 업그레이더를 거쳐 2.0으로 hydrate되고, 업그레이드된 문서의 백테스트 결과가
   1.1 결과와 같다(`raw_weighted` 보존 검증).
6. Phase 종료마다 SoT·책임분리 감사 서브에이전트가 blocking 0.

## 6. 테스트

- **backend**: 2.0 fixture(steps·nodes·json·legacy) 같은 hash golden; 1.1 → 2.0 업그레이드 dict·source
  golden(주석·순서 보존); `RunEnvironment` hash·manifest; combine 3종 수치 검증(1.1 `raw_weighted`
  회귀 포함); boolean 승격·scalar 거부·field_missing이 compile에서 나는지; select/weight union
  hydrate와 inapplicable 경고 축소; 연산자 카탈로그 ↔ 스키마 enum 일치; group 연산자 unsupported
  분기(mock vs duckdb capability).
- **frontend 단위**: `recipe-transactions` property test(임의 steps 문서·임의 연산에 대해
  `parse(apply(op, source)).tree == applyToTree(op, tree)`, 무관한 줄 바이트 보존); `pipeline-projection`
  스키마 fixture 유도; 실행 설정 패널 기본값이 runtime schema에서 오는지; 업그레이드 응답의
  `environment` 적용.
- **e2e**: 빈 문서 → 파이프라인에서 팩터 추가 → 레시피 3단계 → 미리보기 → 백테스트(식별자 0개 단언);
  아이디어 5개 시나리오; 1.1 revision 열기 → 업그레이드 → 실행 설정 채워짐 → 백테스트; 고급 캔버스
  드래그 배선 → YAML 반영 → 되돌리기.

## 7. 롤백

- P1은 PR 단위 revert 가능. 1.1 계약을 바꾸지 않는다.
- P2는 backend만 바꾼다. P2-07(업그레이더) merge 전에는 실 DB에 2.0 revision을 저장하지 않는다.
  P2-01의 브리지 덕에 P2-01만 merge된 상태는 1.1과 완전 호환이다.
- P3 이후는 frontend feature 모듈 추가·재배치라 PR 단위 revert 가능. 파이프라인 탭을 숨기면 1.1
  GUI 편집 화면으로 돌아간다.

## 8. 대안

- **1.1을 유지하고 GUI 층만 추가**: 배관(kind·node_id·output_node_id)이 문서와 hash에 남아 GUI가
  id를 만들어 숨겨야 하고, 실행 설정이 hash에 남아 "같은 전략을 다른 기간에"가 revision을 만든다.
  거부.
- **표현식 문자열 DSL**: parser·source map·pretty-printer·round-trip이 선행되어야 하고 "별도
  authoring 모델 없음" 원칙과 충돌한다(ADR 2026-09-04). steps는 YAML 구조라 이 비용이 없다. 거부.
- **템플릿 갤러리를 주 진입 경로로**: 범용 전략 구성이 목표이므로 사용자를 구현된 아이디어
  목록에 가둔다. 제품 소유자 거부(2026-09-20). 튜토리얼에만 둔다.
- **실행 설정만 분리하고 섹션 재구성은 미룸(1.2)**: hash가 두 번 바뀌고 동결 이력이 두 판 생긴다.
  한 판(2.0)으로 간다.
- **실행 설정을 revision에 함께 저장**: hash에서 뺀 이유와 모순. run manifest에만 기록한다.
