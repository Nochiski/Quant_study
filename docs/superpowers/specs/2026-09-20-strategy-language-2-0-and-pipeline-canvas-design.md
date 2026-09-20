# 설계: schema 1.2(실행 설정 분리)와 그래프 표현

> 작성: 2026-09-20 (같은 날 개정: "기존 YAML은 남긴다. 표현은 YAML과 그래프 둘뿐이다. 기존 YAML
> 규칙 중 일부를 UI로 편입한다")
>
> 상태: Accepted (제품 소유자 결정 2026-09-20: "템플릿이 주가 되면 안 된다. 범용적이고 다양한 전략
> 구성이 목표다. 언어 밖으로 표현할 수 있는 것은 언어에서 뺀다. 기존 YAML은 남긴다")
>
> 선행 계약: [schema 1.1·GUI 편집 설계](./2026-09-17-strategy-schema-1-1-and-gui-editing-design.md)
> (D1 문법, D2 동결 이력, D3 업그레이더, D5 source 트랜잭션을 이 문서가 확장한다),
> [Strategy Authoring Contract ADR](./2026-09-04-strategy-authoring-contract-adr.md)
> (D8 대상 사용자를 이 문서가 개정한다)
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
| 화면 어휘가 스키마 식별자 그대로다. 노드 kind 12, 연산자 23(= `(kind, operator)` 쌍: unary 2·binary 4·time_series 6·cross_sectional 4·group 2·comparison 5), 참조 필드 11이 드롭다운·라벨에 노출되고, 설명이 붙은 property는 121개 중 13개, 그래프 노드는 0개 | `_schema.py:245`가 title에 파이썬 클래스명, `strategy-form-panel.tsx:641`이 `<code>{field.key}</code>` |
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
있다"**를 완료 정의로 되돌린다.

완료 정의의 측정은 감사에 쓴 **퀀트 아이디어 5개**로 고정한다: 12-1 모멘텀, 저PBR+고ROE 결합,
20일 이평 돌파, 거래대금 상위 20% 필터, 변동성 역가중. 다섯 개 모두 그래프 표현만으로 만들어
백테스트까지 도달해야 한다.

템플릿·프리셋은 주 진입 경로가 아니다. 예시 전략은 튜토리얼 문서(매뉴얼)에만 둔다.

### D2. 표현은 두 가지다: YAML과 그래프

| 표현 | 무엇 | 편집 |
|---|---|---|
| YAML | 정본 source. 1.1 문법을 그대로 유지한다(D3의 차이만) | 코드 편집기 |
| 그래프 | 같은 문서의 시각 표현. 전략 전체(파이프라인) → 팩터 하나(레시피) → 노드(고급)의 세 확대 수준 | source 트랜잭션(1.1 spec D5). 별도 편집 모델 없음 |

- Form·JSON 탭은 은퇴한다. Form의 필드 컨트롤은 그래프의 카드 안으로 들어가고(구현은 같은
  `FormFieldsEditor` 재사용), JSON은 YAML 탭의 기존 `포맷` 명령으로 남는다. Diff는 표현이 아니라
  이력 기능이므로 `이력` 영역에 남는다.
- 그래프 표현의 세 수준은 모두 같은 parse tree를 읽는다. 파이프라인은 최상위 섹션, 레시피는
  팩터 하나의 `graph.nodes`가 **단일 입력 체인**일 때의 순서 목록 투영, 고급은 임의 DAG.
- 배관(`kind`·`node_id`·`input_node_id`·`output_node_id`)은 언어에 남지만 **그래프 표현에서는
  사용자에게 보이지 않는다.** 레시피 빌더가 노드를 추가할 때 kind는 연산자에서, node_id는
  `<operator>_<n>`으로, 입력은 직전 단계로, 출력은 마지막 단계로 채운다(1.1 `addNode` 규칙 확장).
  고급 화면의 접힌 "식별자" 영역에서만 보인다.
- **다중 입력 연산자의 부가 입력은 항상 새 소스 잎 노드다**(정본). 레시피 빌더는 `binary`·
  `comparison`처럼 입력이 둘인 연산자를 추가할 때 부가 입력을 데이터 필드 picker로 물어 `field`·
  `constant`·`parameter` 잎 노드를 **새로 만든다**. 체인 머리를 다시 참조하는 형태(`left: close`,
  `right: ma20`처럼 `close`를 두 곳에서 읽는 그래프)는 손으로 쓸 수 있지만 **비체인으로 판정해
  고급 수준으로 보낸다.** 같은 아이디어가 문서 두 벌로 갈리지 않게 하는 규칙이며, `ideas/*.yaml`
  fixture는 레시피 빌더 산출 형태를 따른다.
- 체인 판정 규칙(정본): 소스 노드 하나로 시작해 각 노드가 직전 노드만 참조하고 `output_node_id`가
  마지막 노드이면 체인이다. 다중 입력 노드는 **한 입력이 체인 꼬리이고 나머지 입력이 전부 체인
  밖 잎일 때만** 체인으로 본다. 잎은 입력이 없는 소스 노드(`field`·`constant`·`parameter`)이고
  체인 단계로 세지 않는다. `conditional`처럼 입력이 셋인 노드도 같은 규칙을 쓴다.
- 아이디어 3(20일 이평 돌파)의 정본 노드 형태는 잎 4개다: `close`(field `price.close`) →
  `ma20`(time_series mean, window 20, input `close`) → 잎 `close_2`(field `price.close`) →
  `breakout`(comparison gt, `left_node_id: close_2`, `right_node_id: ma20`, 출력). 마지막 노드가
  다중 입력이고 한 입력(`ma20`)이 체인 꼬리, 나머지(`close_2`)가 잎이므로 체인이다.

### D3. 기존 YAML 규칙 중 실행 환경을 UI로 편입한다 (schema 1.2)

1.2 문서는 다음만 1.1과 다르다. 나머지 키 이름·값·중첩·그래프 문법은 1.1과 같다.

| # | 변경 | 1.1 | 1.2 |
|---|---|---|---|
| S1 | `data` 섹션 제거 | `data.market`·`frequency`·`start`·`end`·`universe_id` 필수 | 키 자체가 unknown key. 값은 실행 설정(D6 `RunEnvironment`) |
| S2 | `execution` 섹션 제거 | `timing`·`participation_rate`·`fee_bps`·`slippage_bps` | unknown key. 실행 설정으로 |
| S3 | `graph.missing_policy` 제거 | 팩터 그래프마다 4개 선택지 | unknown key. 실행 설정의 `missing` 기본값 하나 |
| S4 | `signal.normalization` 추가 | 없음(원시값 가중 합) | `none`·`rank`·`zscore`. 새 문서 기본값 `rank`. 업그레이드 문서는 `none` 명시(의미 보존) |
| S5 | `eligibility.rules[]`에 횡단면 규칙 추가 | `field_id`·`operator`·`value`만, `operator`가 공유 `ComparisonOperator` | 전용 `EligibilityOperator`(gt·gte·lt·lte·eq·top_percent·top_count)로 분리. `top_*`의 `value`는 비율·개수 |
| S6 | `risk.risk_factor_id` 추가 | `risk_field_id`(데이터 필드만) | 팩터 id로 역가중. `risk_field_id`와 동시 지정은 error. 참조 팩터는 합성 점수에서 제외 |
| S7 | `saved_factor`·`saved_subgraph` 노드 제거 | 스키마에 있으나 실행 거부 | 노드 union에서 제거(M8 라이브러리가 되살릴 때 재추가) |

- 최상위 필수 키는 `schema_version`·`title` **둘**이다. `factors`는 생략해도, 빈 배열이어도 구조
  오류가 아니다(모델이 `factors: tuple[FactorSignal, ...] = ()`라 `_hydrate.py`가
  `structure.missing_field`를 내지 않는다). 두 경우 모두 semantic `strategy.factor.required`를 내서,
  새 전략이 "구조 오류"가 아니라 "팩터를 추가하세요"로 시작한다. P4-04의 시작 문서
  `schema_version: "1.2"\ntitle: ""\n`가 구조 오류 0건으로 열리는 근거가 이것이다.
- canonical payload와 hash 알고리즘(ADR D3)은 그대로다. **hash가 이제 전략 논리만 덮는다.** 같은
  전략을 다른 기간·유니버스·수수료로 돌려도 revision이 늘지 않는다.
- `CURRENT_SCHEMA_VERSION = "1.2"`. 1.0·1.1 문서는 D7의 업그레이드 경로로만 들어온다.
- 1.2 최소 문서:

```yaml
schema_version: "1.2"
title: "퀄리티 모멘텀"
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

**S5 횡단면 eligibility의 의미(정본).** 현재 `EligibilityRule.operator`는 `domain/strategy/_models.py`의
공유 `ComparisonOperator`이고, `domain/portfolio/_compiler.py`의 `_compare`는 마지막 줄이 catch-all
`return value == threshold`다. `top_percent`를 공유 enum에 얹으면 예외가 아니라 조용한 오필터가 된다.

- `EligibilityRule.operator`는 **전용 `EligibilityOperator`**(`gt`·`gte`·`lt`·`lte`·`eq`·
  `top_percent`·`top_count`)다. `ComparisonOperator`와 값이 겹쳐도 타입을 공유하지 않는다.
- `_compare`는 **exhaustive**로 바꾼다. 미지 연산자는 조용히 `==`로 떨어지지 않고 raise한다.
- **모집단**: 기준일 프레임에서 실행 설정 유니버스의 멤버 중 절대 규칙(`gt`~`eq`)을 전부 통과했고
  그 규칙의 `field_id` 값이 있는 종목. 결측 종목은 탈락시키고 **분모에서도 뺀다.**
- **동점**: 값 내림차순, 같으면 `security_id` 오름차순으로 정렬한 뒤 cut한다. 정렬이 전순서라
  컷이 결정적이다.
- **평가 지점**: 프레임 컴파일을 2-pass로 바꾼다. 1-pass가 절대 규칙으로 후보를 거르고, 2-pass가
  남은 후보의 횡단면 순위로 `top_*`를 적용한다. 절대 규칙과 횡단면 규칙은 AND다.

**S6 `risk.risk_factor_id`의 의미(정본).** `_compiler.py:526-570`은 `spec.factors` 전부를 합성에
넣고, `:838-844`의 `risk` 분기는 원시 필드만 읽는다.

- 참조된 팩터는 **합성 점수에서 제외한다**(그 팩터의 `weight`를 무시하고 분모 `Σ|weight|`에서도
  뺀다). 사용자가 "가중만 바꿨다"고 믿는 동안 종목 선정이 바뀌는 것을 막는다. 제외했다는 사실은
  정보 진단 `strategy.risk.risk_factor_excluded`(warning)로 알린다.
- 역가중은 **`signal.normalization` 적용 이전의 원시 출력**을 쓴다. `rank`(1.2 기본값) 출력을
  역수로 쓰면 무의미한 가중이 되기 때문이다. 값은 `PortfolioObservation.factor_values`에 있다.
- 참조 팩터 값이 `<= 0`이면 기존 `MISSING_RISK` 탈락 분기를 그대로 쓴다(`_compiler.py:841-843`).

### D4. `signal.normalization`이 결합 전 정규화를 소유한다

- `composite = Σ(sign(direction) × weight × norm(x)) / Σ|weight|`. `norm`은 `rank`(횡단면 0~1 순위),
  `zscore`(횡단면 표준화), `none`(항등, 1.1 의미).
- `none`이고 팩터 출력 단위(`NodeContract.unit`)가 서로 다르면 `strategy.signal.unit_mismatch`
  warning. 팩터가 하나면 경고하지 않는다.
- `risk.risk_factor_id`가 가리키는 팩터는 이 합성에서 빠진다(D3 S6). `Σ`와 분모 `Σ|weight|` 양쪽에서
  뺀다. 역가중이 읽는 값은 `norm`을 거치지 않은 원시 출력이다.
- 그래프의 "알파 팩터" 단계 "점수 합치는 방법" 카드가 이 필드를 "순위로 맞춘 뒤 가중 합 / 표준화 뒤 가중 합 / 원시값
  가중 합(주의)"으로 보인다.

### D5. 저장 게이트와 실행 게이트를 하나로 합친다

- compile 서비스가 연결된 equity 어댑터의 필드 메타데이터를 `validate_strategy`에 넘긴다.
  `field_missing`(field_id 오타)이 저장 전에 난다. 어댑터가 없는 컨텍스트(CLI·테스트)는 지금과 같다.
- 팩터 출력 타입 검사를 compile로 앞당긴다. boolean 출력은 팩터 경계에서 0/1 `numeric_series`로
  승격한다(canonical 그래프 끝에 승격 노드, 사용자 문서 불변). scalar 출력은
  `strategy.factor.output_type` error. `_reject_non_numeric_factor_outputs`는 남기되 compile 통과
  문서에서 발화하면 결함이다(property 테스트).
- 실데이터 어댑터가 `GROUP_SERIES` 필드를 제공하기 전까지 `group` 노드는 연산자 카탈로그에서
  `availability: unsupported`이고 그래프 팔레트에 나오지 않는다. 문서에 쓰면 compile이
  `strategy.operator.unsupported` error(어댑터 capability 조회).
- 실행 차단 판정은 계속 backend compile diagnostics의 error severity 하나다.

### D6. 실행 설정(`RunEnvironment`)

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

- owner는 `domain/backtest/_models.py`다(전략이 아니라 실행의 사실). `BacktestRunSpec`, portfolio
  preview 요청, trace 요청이 `environment` 필드로 같은 타입을 받는다.
- run manifest는 `spec_hash`와 별개로 `environment_hash`를 기록한다. 실행 결과 캐시 키에도 들어간다.
- **`RunEnvironment.missing`은 `build_factor_execution_plan`의 인자로 들어가 `plan_hash`에 계속
  포함된다.** 1.1에서는 `FactorGraph.missing_policy`가 plan payload에 들어가 팩터 행렬 캐시 키를
  갈랐다(`domain/factor/_planning.py:122-131`, `:153-160`). 섹션만 지우고 환경 값을 plan 빌더에
  넘기지 않으면 `missing: drop` 실행과 `missing: zero` 실행이 같은 캐시 키를 공유해 두 번째 실행이
  첫 번째의 결측 처리 결과를 조용히 재사용한다. 결측 정책은 계속 plan의 일부다.
- frontend 실행 설정 패널이 필드를 소유한다. 기본값은 **실행 설정 스키마**
  `GET /api/v1/run-environments/schema`가 주는 JSON Schema의 default이며, 전략 authoring runtime
  schema(`strategy_document_schema()`, `domain/strategy` 소유)와는 다른 물건이다. 마지막 사용값은
  전략별 local UI state(`localStorage`). 서버는 run manifest에만 기록하고 전략 revision에는
  저장하지 않는다(hash에서 뺀 이유).
- 전환 규칙: P2-01은 `environment`를 optional로 받고 없으면 `spec.data`·`spec.execution`·
  `graph.missing_policy`에서 만든다. 브리지는 `RunEnvironment`의 owner인 **`domain/backtest`**가
  소유한다(`environment_from_legacy_spec(spec) -> RunEnvironment`). `application/backtest_run`에 두면
  `portfolio_design → backtest_run` 화살표가 생겨 기존 `backtest_run → portfolio_design`과 순환이
  되고 `backend/tests/architecture/test_dependency_direction.py`가 실패한다. 경계 규칙은
  application → application을 "다른 유스케이스의 outgoing port를 소비할 때만" 허용하는데 브리지는
  port가 아니라 로직이다. P2-03이 섹션을 모델에서 지우면 `environment`가 필수가 된다.

### D7. 1.1 → 1.2 업그레이드와 동결 이력

현재 `domain/strategy/_upgrade.py`는 **단일 버전 변환기**다. `UPGRADE_STEPS`가 평탄한 step 튜플이고
(`:99-104`), `_step_schema_version`이 곧장 `CURRENT_SCHEMA_VERSION`을 찍으며(`:58-59`),
`is_legacy_document`가 `== "1.0"`을 단정하고(`:107-108`), `apply_upgrade_steps`가 1.0이 아니면
`NotALegacyDocumentError`를 낸다(`:111-118`). step만 이어 붙이면 1.0 문서가 1.1 step과 1.2 step을 한
번에 맞아 중간 상태 검증이 사라지고, 1.1 입력은 거부된다. 그래서 **버전 디스패치로 다시 쓴다.**

- `UPGRADE_STEPS: Mapping[str, tuple[UpgradeStep, ...]]` — 키는 from-version(`"1.0"`·`"1.1"`).
- 공개 API는 `upgrade_document(tree) -> UpgradeOutcome(tree, environment, warnings)` 하나다. 문서의
  `schema_version`에서 시작해 `CURRENT_SCHEMA_VERSION`까지 체인을 **순서대로** 적용하고, 각 중간
  단계마다 그 버전으로 `schema_version`을 찍고 검증한다.
- 심볼 교체: `LEGACY_SCHEMA_VERSION`(단수) → `FROZEN_SCHEMA_VERSIONS`, `is_legacy_document` →
  `is_upgradeable_document`, `upgrade_document_1_0` → `upgrade_document`, `NotALegacyDocumentError` →
  `NotUpgradeableDocumentError`. `is_frozen_schema_version`은 이미 `!= CURRENT_SCHEMA_VERSION`이라
  **변경하지 않는다**(`:27-34`).
- facade `domain/strategy/facade/document.py`의 공개 심볼과 호출자 세 곳
  (`adapters/outbound/document_codec/_upgrade_source.py`,
  `adapters/outbound/strategy_sqlite/_record_codec.py`,
  `application/strategy_authoring/_service.py`)을 같은 PR에서 갱신한다.
- dict 경로·source 경로(ruamel round-trip)는 같은 step 맵을 쓰고 drift fail-closed(1.1 spec D3 유지).
- 변환: `schema_version` → `"1.2"`; `data`·`execution`·`graph.missing_policy` 제거 후 **응답의
  `environment`로 반환**(팩터별 `missing_policy`가 서로 다르면 첫 팩터 값을 쓰고 warning);
  `signal.normalization: none` 명시; `saved_factor`·`saved_subgraph` 노드가 있으면 업그레이드 거부
  (`strategy_document.upgrade_unsupported_node`, 실행 경로가 원래 없었으므로 잃는 것이 없다).
- `POST /strategy-documents/upgrade` 응답에 `environment` 추가. frontend는 원문을 편집기 범위 교체
  한 번으로 적용하고 `environment`로 실행 설정 패널을 채운다.
- 1.1 revision은 1.0과 같은 동결 이력이다. `is_frozen_schema_version`은 이미 `!= CURRENT`라 변경
  없이 동결된다. saved-reference backtest는 `422 strategy_revision_requires_upgrade`.
- golden 파일의 역할을 파일별로 고정한다. `quality_momentum.v1_1.commented.yaml`은 **이미
  1.0 → 1.1 source 업그레이드의 기대 출력**이고 세 테스트가 단언한다
  (`tests/application/test_strategy_authoring_upgrade.py:69`,
  `tests/contract/test_document_upgrade_source.py:50`,
  `tests/integration/test_strategy_document_upgrade_http_api.py:33`). 이름을 겹쳐 쓰지 않는다.
  - `quality_momentum.v1_1.commented.yaml` — 1.0 → 1.1 **중간 단계** 고정용(현행 유지).
  - `quality_momentum.v1_2.commented.yaml` — 1.0 문서의 **최종** 기대 출력(신규). 위 세 단언이 이
    파일로 옮겨간다.
  - `quality_momentum.v1_1.yaml` — 보존된 1.1 원본(현행 `quality_momentum.yaml` 복사, 1.1 → 1.2
    업그레이드 입력).
- golden 단언: 1.0 원본 → 1.2 원문(주석·순서 보존) → dict 경로와 tree 동일. 업그레이드된 문서의
  preview 결과가 1.1 결과와 같다(`normalization: none` 보존).

### D8. 연산자 카탈로그 (backend owner)

팩터에는 `FactorDefinition.description`이 있지만 연산자에는 없다. `domain/factor/_operators.py`에
`OperatorDefinition(operator, kind, arity, params, output_type_rule, unit_rule, availability,
description_key, formula_key, example)`을 두고 `GET /api/v1/strategy-documents/operators`와 runtime
schema(`x-description-key`, `x-operator`)로 내려준다. **레지스트리 키는 `(kind, operator)` 쌍이고
항목은 23개다.** `rank`가 `cross_sectional`과 `group` 양쪽에 있어 이름 단독 키는 충돌하며,
`ComparisonNode.operator`(`FactorComparisonOperator` 5개)도 팔레트·설명 대상이라 빼지 않는다. 문장은 frontend i18n(`operator.<name>.*`)이
렌더한다(적용 조건 문장과 같은 소유 규칙: 키는 backend, 문장은 소비자별).

레시피 팔레트, 고급 캔버스 팔레트, Contract Inspector가 같은 카탈로그를 읽는다. frontend에 연산자
목록·설명을 손으로 적지 않는다.

### D9. 그래프 표현의 세 수준은 전부 source 트랜잭션 위에 얹힌다 (1.1 spec D5 유지)

| 수준 | 투영 입력 | 편집 |
|---|---|---|
| 파이프라인(전략 전체) | runtime schema × parse tree × compile 진단 → 통상 퀀트 프레임워크의 단계 모델(`pipeline-projection.ts`): 1 유니버스(Universe) `eligibility`, 2 알파 팩터(Alpha) `factors`+`signal`, 3 포트폴리오 구성(Portfolio) `portfolio`, 4 리스크 제약(Risk) `risk`, 5 실행(Execution) = 실행 설정 띠(문서 밖). YAML 섹션과 1:1이다. 문서 전체를 한국어 한 문장으로 요약한 문장(`strategy-summary`)도 같은 투영이 만든다 | 카드 컨트롤은 Form 필드 컨트롤 재사용(`replaceScalar`·`insertKey`·`remove`). 팩터 추가는 빈 그래프 팩터 `insertItem` |
| 레시피(팩터 하나) | `graph.nodes`가 D2의 체인 판정 규칙을 만족하면 순서 목록(`recipe-projection.ts`). 다중 입력 노드는 부가 입력이 전부 체인 밖 잎일 때만 체인이고, 체인 머리 재참조는 비체인이다. 아니면 "고급에서 편집" 안내 | `recipe-transactions.ts`: 단계 추가 = `addNode`(kind·id·입력·출력 자동, 다중 입력 연산자는 부가 입력 잎을 새로 만든다) + 다음 노드 재배선, 삭제 = `remove` + 재배선, 이동 = `*_node_id` 재배선, 파라미터 = `replaceScalar`. 여러 연산은 `planSourceOperations`로 한 undo 단계 |
| 고급(임의 DAG) | `graph.nodes` + backend plan | 1.1 `graph-transactions.ts` 유지. 드래그 배선은 `*_node_id` `replaceScalar`. 좌표는 local UI state |
| 실행 설정 띠 | 실행 설정 스키마 `GET /api/v1/run-environments/schema`(전략 authoring runtime schema와 별개) | 문서 밖. `run-settings.ts` 필드 |
| 기준일 미리보기 | 기존 trace API(`debug-strategy`) | 읽기 전용 |

- 탭은 **그래프 · YAML** 둘이다. 새 전략과 revision 열기의 기본 탭은 그래프. 이력(revision·diff)은
  탭이 아니라 기존 revision 화면 영역.
- 문제 목록과 "검증 통과" 배지는 탭과 무관하게 렌더된다. 진단은 pointer로 해당 카드·단계에 붙는다.
- 되돌리기·다시 실행은 툴바 버튼과 전역 단축키다. 편집기가 hidden이어도 동작한다.
- 단계 구조는 통상 퀀트 전략 프로그램(유니버스 → 알파 → 포트폴리오 구성 → 리스크 → 실행)을 따른다.
  표준 용어를 단계 이름으로 쓰고(한글 + 영문 소제목), 그 아래 한 줄 쉬운 설명을 둔다. 새 어휘를
  만들지 않는다.
- 화면 문구 규칙: 단계 이름은 위 표준 용어이고,
  카드의 컨트롤은 한국어 문장 안에 들어가 읽힌다("거래대금이 [10]억 원 [이상인] 종목만"). 용어만 있는
  문구(순위·완충·역가중)는 쓰지 않고 결과로 설명한다("20위 밖으로 5위까지 밀려도 유지"). 캔버스
  맨 위에 문서 전체를 한 문장으로 요약해 보여 주고 카드가 바뀌면 문장이 바뀐다. 문장은 i18n 틀에
  값을 끼운 것이며 frontend가 필드 목록을 손으로 적지는 않는다(스키마 `x-stage`와 i18n 키).
- 그래프 탭(파이프라인·레시피 수준)에는 YAML 식별자가 나오지 않는다. 확인: 그 DOM 텍스트에
  `_id`·`_node`·`kind:` 패턴이 없다는 e2e 단언. 고급 수준은 접힌 영역에서만 노출.

### D10. 상태 소유권 (1.1 spec D8 갱신)

| 상태 | 소유자 |
|---|---|
| 전략 논리(spec, spec_hash) | backend compile, `StrategySpec` 1.2 |
| 실행 설정 | `RunEnvironment`(domain/backtest). 편집 중 값은 frontend 실행 설정 패널, 실행된 값은 run manifest |
| 1.0 → 1.1 → 1.2 변환 | `domain/strategy/_upgrade.py` `UPGRADE_STEPS` |
| 연산자 정의·설명 키·가용성 | `domain/factor/_operators.py` |
| 정규화 방식과 합성 공식 | `domain/portfolio/_compiler.py`가 `signal.normalization`을 읽는다 |
| 단일 입력 체인 판정·순서 투영 | `features/edit-strategy/model/recipe-projection.ts` |
| 4단계 투영 | `features/edit-strategy/model/pipeline-projection.ts` |
| 그래프 좌표·펼침·선택 | frontend local UI state |

### D11. 규칙·문서 갱신

| 위치 | 변경 |
|---|---|
| `.claude/rules/strategy-workbench-sot.md` (P0-01) | "전략 의미" 행에 "표현은 YAML과 그래프 둘" 명시, "실행 설정"·"연산자 정의"·"그래프 표현 투영" 행 신설(owner는 실제 파일, 미구현은 괄호 표기), 업그레이드 행 제목을 "1.0 → 1.1 → 1.2"로 예약, `paths:` frontmatter에 이 패키지 추가, 금지 절 캐시 키 문장에 `environment_hash`. 금지 절의 DSL 문장 유지 |
| `.claude/rules/strategy-workbench-sot.md` (후속 PR) | 실행 설정·연산자·투영 행의 "(아직 없음)" 표기 해제는 각 구현 PR(P2-01·P1-03·P4-01·P5-01), 금지 절 "JSON/Form/Graph/Diff projection" 문장 갱신은 P4-04, 1.2 업그레이드 step 반영은 P2-09 |
| ADR 2026-09-04 | D8 대상 사용자·완료 정의 개정 링크 |
| 1.1 spec | 머리말에 이 문서로의 확장 링크 |
| 로드맵 | 완료 정의 원문 복귀, M8 "custom formula editor"는 여전히 non-goal |
| `docs/manual/strategy-workbench/README.md`, `README.md`, `frontend/README.md`, `backend/FACTORS.md` | 1.2 문법, 실행 설정, 그래프 화면, 예시 전략 5개(튜토리얼) |
| fixture | `quality_momentum.yaml` 1.2, `.v1_1.yaml`(1.1 원본, 업그레이드 입력), `.v1_1.commented.yaml`(1.0 → 1.1 중간 단계), `.v1_2.commented.yaml`(1.0 최종 golden), `ideas/*.yaml` 5개(완료 정의, 레시피 빌더 산출 형태) |

## 3. Non-goals

- 새 팩터 표기(`steps` 등), 표현식 문자열 DSL, 단위 literal, YAML anchor/alias. **기존 YAML 그래프
  문법은 그대로다.**
- `portfolio`·`risk`·`signal` 섹션 재구성(discriminated union). 1.1의 flat 유지 + 적용 조건 경고
- Form·JSON 탭의 유지(은퇴한다). Diff는 이력으로 남는다
- `saved_factor`·`saved_subgraph`·재사용 팩터 라이브러리(M8)
- 파라미터 탐색 UI(M6)
- KRX 외 시장, 일별 외 빈도(실행 설정 enum은 하나뿐이어도 남긴다)
- 다중 사용자 동시 편집, 자동 merge
- 1.0·1.1 revision의 in-place 재해시나 DB 마이그레이션
- Rust engine 변경
- 자연어 → 전략 생성(LLM)

## 4. Phase와 PR 스택 개요

Phase 1은 규칙 변경 없이 1.1 위에서 끝나며 1.2 뒤에도 그대로 남는다. Phase 2~3이 언어(실행 설정
분리), Phase 4~6이 그래프 표현이다. 상세 acceptance는 WORKFLOW.md, 상태는 PLAN.md가 소유한다.

| Phase | 목표 | PR |
|---|---|---|
| P0 | 기획 패키지·계약 문서 개정 | P0-01 |
| P1 | 화면 안에서 끝나는 마찰 제거(진단·되돌리기·한글 어휘·연산자 카탈로그·조용한 실패) | P1-01 ~ P1-05 |
| P2 | backend schema 1.2(실행 설정 분리, 추가 필드, 단일 게이트, 업그레이더) | P2-01 ~ P2-09 |
| P3 | frontend 1.2 적응(SDK·실행 설정 패널·업그레이드·e2e·매뉴얼) | P3-01 ~ P3-03 |
| P4 | 그래프 표현 1수준 파이프라인(4단계 카드, 팩터 카드, 미리보기, 탭 둘로, 빈 화면 e2e) | P4-01 ~ P4-04 |
| P5 | 그래프 표현 2수준 레시피(체인 투영·트랜잭션, 팔레트, 결과 미리보기, 아이디어 5개 e2e) | P5-01 ~ P5-03 |
| P6 | 그래프 표현 3수준 고급 노드 캔버스(라이브러리 ADR, 드래그 배선, 노드 위 진단, 마감) | P6-01 ~ P6-03 |

## 5. 완료 정의(initiative)

1. 감사의 아이디어 5개가 각각 그래프 탭(파이프라인·레시피 수준)만으로 빈 문서에서 만들어져
   백테스트 실행까지 도달한다(e2e 5건, `fixtures/strategy_documents/ideas/*.yaml`이 그 결과 원문과
   같은 hash).
2. 그래프 탭 파이프라인·레시피 수준 DOM에 YAML 식별자가 없다(e2e 단언).
3. compile "검증 통과" 문서가 preview·backtest에서 422가 나지 않는다(fixture 전수 + property).
4. 1.1 fixture 전부가 업그레이더를 거쳐 1.2로 hydrate되고, 업그레이드된 문서의 백테스트 결과가
   1.1 결과와 같다(`normalization: none` 보존 검증).
5. 같은 전략을 다른 실행 설정으로 돌려도 `spec_hash`가 같다(e2e).
6. Phase 종료마다 SoT·책임분리 감사 서브에이전트가 blocking 0.

## 6. 테스트

- **backend**: 1.2 fixture(yaml·json·legacy·minimal) 같은 hash golden; 1.1 → 1.2 업그레이드 dict·source
  golden(주석·순서 보존); `RunEnvironment` hash·manifest; normalization 3종 수치 검증(1.1 `none`
  회귀 포함); boolean 승격·scalar 거부·field_missing이 compile에서 나는지; 횡단면 eligibility 규칙;
  `risk_factor_id`; 연산자 카탈로그 ↔ 스키마 enum 일치; group 연산자 unsupported 분기.
- **frontend 단위**: `recipe-transactions` property test(임의 단일 입력 체인 문서·임의 연산에 대해
  `parse(apply(op, source)).tree == applyToTree(op, tree)`, 체인 불변식 유지, 무관한 줄 바이트 보존);
  `recipe-projection` 체인 판정(분기·다중 입력·미참조 노드는 비체인); `pipeline-projection` 스키마
  fixture 유도; 실행 설정 패널 기본값이 runtime schema에서 오는지; 업그레이드 응답 `environment` 적용.
- **e2e**: 빈 문서 → 파이프라인에서 팩터 추가 → 레시피 3단계 → 미리보기 → 백테스트(식별자 0개
  단언); 아이디어 5개; 1.1 revision 열기 → 업그레이드 → 실행 설정 채워짐 → 백테스트; 고급 캔버스
  드래그 배선 → YAML 반영 → 되돌리기.

## 7. 롤백

- P1은 PR 단위 revert 가능. 1.1 계약을 바꾸지 않는다.
- P2는 backend만 바꾼다. P2-09(업그레이더) merge 전에는 실 DB에 1.2 revision을 저장하지 않는다.
  P2-01의 브리지 덕에 P2-01만 merge된 상태는 1.1과 완전 호환이다.
- P3 이후는 frontend feature 모듈 추가·재배치라 PR 단위 revert 가능. 그래프 탭을 숨기고 Form 탭을
  되살리면 1.1 GUI 편집 화면으로 돌아간다(P4-04 전까지 Form 코드는 남아 있다).

## 8. 대안

- **팩터 표기를 새로 만든다(`steps`)**: 배관이 문서에서 사라지고 hash가 표기와 무관해지지만 언어가
  둘이 되고(steps·nodes) 역변환·확장 엔드포인트가 필요하다. 제품 소유자가 "기존 YAML은 남긴다"로
  거부(2026-09-20). 배관 숨기기는 그래프 UI가 맡는다.
- **`portfolio`·`risk`·`signal`을 역할별 섹션으로 재구성**: 모델·컴파일러·UI가 한꺼번에 바뀌고
  기존 YAML 사용자의 문서가 전부 바뀐다. 실행 설정 분리만으로 목표(hash에서 환경 제거)가 달성되므로
  미룬다.
- **표현식 문자열 DSL**: ADR 2026-09-04 non-goal 유지.
- **템플릿 갤러리를 주 진입 경로로**: 제품 소유자 거부. 튜토리얼에만 둔다.
- **실행 설정을 revision에 함께 저장**: hash에서 뺀 이유와 모순. run manifest에만 기록한다.
- **Form 탭 유지**: 표현이 셋이 되어 "YAML과 그래프 둘"이라는 결정과 어긋난다. Form의 컨트롤은
  그래프 카드가 재사용한다.
