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

### D3. 기존 YAML 규칙 중 실행 환경을 UI로 편입한다 (schema 1.2)

1.2 문서는 다음만 1.1과 다르다. 나머지 키 이름·값·중첩·그래프 문법은 1.1과 같다.

| # | 변경 | 1.1 | 1.2 |
|---|---|---|---|
| S1 | `data` 섹션 제거 | `data.market`·`frequency`·`start`·`end`·`universe_id` 필수 | 키 자체가 unknown key. 값은 실행 설정(D6 `RunEnvironment`) |
| S2 | `execution` 섹션 제거 | `timing`·`participation_rate`·`fee_bps`·`slippage_bps` | unknown key. 실행 설정으로 |
| S3 | `graph.missing_policy` 제거 | 팩터 그래프마다 4개 선택지 | unknown key. 실행 설정의 `missing` 기본값 하나 |
| S4 | `signal.normalization` 추가 | 없음(원시값 가중 합) | `none`·`rank`·`zscore`. 새 문서 기본값 `rank`. 업그레이드 문서는 `none` 명시(의미 보존) |
| S5 | `eligibility.rules[]`에 횡단면 규칙 추가 | `field_id`·`operator`·`value`만 | `operator`에 `top_percent`·`top_count` 추가(`value`가 비율·개수) |
| S6 | `risk.risk_factor_id` 추가 | `risk_field_id`(데이터 필드만) | 팩터 id로 역가중. `risk_field_id`와 동시 지정은 error |
| S7 | `saved_factor`·`saved_subgraph` 노드 제거 | 스키마에 있으나 실행 거부 | 노드 union에서 제거(M8 라이브러리가 되살릴 때 재추가) |

- 최상위 필수 키는 `schema_version`·`title`·`factors` 셋이다. 빈 `factors: []`는 structural error가
  아니라 semantic error(`strategy.factor.required`)로 바꿔, 새 전략이 "구조 오류"가 아니라 "팩터를
  추가하세요"로 시작한다.
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

### D4. `signal.normalization`이 결합 전 정규화를 소유한다

- `composite = Σ(sign(direction) × weight × norm(x)) / Σ|weight|`. `norm`은 `rank`(횡단면 0~1 순위),
  `zscore`(횡단면 표준화), `none`(항등, 1.1 의미).
- `none`이고 팩터 출력 단위(`NodeContract.unit`)가 서로 다르면 `strategy.signal.unit_mismatch`
  warning. 팩터가 하나면 경고하지 않는다.
- 그래프의 "합쳐서 고른다" 카드가 이 필드를 "순위로 맞춘 뒤 가중 합 / 표준화 뒤 가중 합 / 원시값
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
- run manifest는 `spec_hash`와 별개로 `environment_hash`를 기록한다. 캐시 키에도 들어간다.
- frontend 실행 설정 패널이 필드를 소유한다. 기본값은 runtime schema `RunEnvironment` default,
  마지막 사용값은 전략별 local UI state(`localStorage`). 서버는 run manifest에만 기록하고 전략
  revision에는 저장하지 않는다(hash에서 뺀 이유).
- 전환 규칙: P2-01은 `environment`를 optional로 받고 없으면 `spec.data`·`spec.execution`·
  `graph.missing_policy`에서 만든다(브리지). P2-02가 섹션을 모델에서 지우면 필수가 된다.

### D7. 1.1 → 1.2 업그레이드와 동결 이력

- `domain/strategy/_upgrade.py`의 `UPGRADE_STEPS`에 1.1 → 1.2 step을 잇는다. 1.0 문서는 1.0 → 1.1 →
  1.2 순서. dict 경로·source 경로(ruamel round-trip) 같은 step, drift fail-closed(1.1 spec D3 구조 유지).
- 변환: `schema_version` → `"1.2"`; `data`·`execution`·`graph.missing_policy` 제거 후 **응답의
  `environment`로 반환**(팩터별 `missing_policy`가 서로 다르면 첫 팩터 값을 쓰고 warning);
  `signal.normalization: none` 명시; `saved_factor`·`saved_subgraph` 노드가 있으면 업그레이드 거부
  (`strategy_document.upgrade_unsupported_node`, 실행 경로가 원래 없었으므로 잃는 것이 없다).
- `POST /strategy-documents/upgrade` 응답에 `environment` 추가. frontend는 원문을 편집기 범위 교체
  한 번으로 적용하고 `environment`로 실행 설정 패널을 채운다.
- 1.1 revision은 1.0과 같은 동결 이력이다. `is_frozen_schema_version`은 이미 `!= CURRENT`라 변경
  없이 동결된다. saved-reference backtest는 `422 strategy_revision_requires_upgrade`.
- golden: `quality_momentum.v1_1.commented.yaml` → 1.2 원문(주석·순서 보존) → dict 경로와 tree 동일.
  업그레이드된 문서의 preview 결과가 1.1 결과와 같다(`normalization: none` 보존).

### D8. 연산자 카탈로그 (backend owner)

팩터에는 `FactorDefinition.description`이 있지만 연산자에는 없다. `domain/factor/_operators.py`에
`OperatorDefinition(operator, kind, arity, params, output_type_rule, unit_rule, availability,
description_key, formula_key, example)`을 두고 `GET /api/v1/strategy-documents/operators`와 runtime
schema(`x-description-key`, `x-operator`)로 내려준다. 문장은 frontend i18n(`operator.<name>.*`)이
렌더한다(적용 조건 문장과 같은 소유 규칙: 키는 backend, 문장은 소비자별).

레시피 팔레트, 고급 캔버스 팔레트, Contract Inspector가 같은 카탈로그를 읽는다. frontend에 연산자
목록·설명을 손으로 적지 않는다.

### D9. 그래프 표현의 세 수준은 전부 source 트랜잭션 위에 얹힌다 (1.1 spec D5 유지)

| 수준 | 투영 입력 | 편집 |
|---|---|---|
| 파이프라인(전략 전체) | runtime schema × parse tree × compile 진단 → 4단계 모델(`pipeline-projection.ts`): 거른다 `eligibility`, 점수를 매긴다 `factors`, 합쳐서 고른다 `signal`+`portfolio`, 비중을 준다 `risk`+`portfolio.weighting` | 카드 컨트롤은 Form 필드 컨트롤 재사용(`replaceScalar`·`insertKey`·`remove`). 팩터 추가는 빈 그래프 팩터 `insertItem` |
| 레시피(팩터 하나) | `graph.nodes`가 단일 입력 체인(각 노드가 직전 노드만 참조, 마지막이 출력)이면 순서 목록(`recipe-projection.ts`). 아니면 "고급에서 편집" 안내 | `recipe-transactions.ts`: 단계 추가 = `addNode`(kind·id·입력·출력 자동) + 다음 노드 재배선, 삭제 = `remove` + 재배선, 이동 = `*_node_id` 재배선, 파라미터 = `replaceScalar`. 여러 연산은 `planSourceOperations`로 한 undo 단계 |
| 고급(임의 DAG) | `graph.nodes` + backend plan | 1.1 `graph-transactions.ts` 유지. 드래그 배선은 `*_node_id` `replaceScalar`. 좌표는 local UI state |
| 실행 설정 띠 | `RunEnvironment` runtime schema | 문서 밖. `run-settings.ts` 필드 |
| 기준일 미리보기 | 기존 trace API(`debug-strategy`) | 읽기 전용 |

- 탭은 **그래프 · YAML** 둘이다. 새 전략과 revision 열기의 기본 탭은 그래프. 이력(revision·diff)은
  탭이 아니라 기존 revision 화면 영역.
- 문제 목록과 "검증 통과" 배지는 탭과 무관하게 렌더된다. 진단은 pointer로 해당 카드·단계에 붙는다.
- 되돌리기·다시 실행은 툴바 버튼과 전역 단축키다. 편집기가 hidden이어도 동작한다.
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
| `.claude/rules/strategy-workbench-sot.md` | "전략 의미" 행에 그래프 표현 세 수준·투영 owner 추가, "실행 설정" 행 신설, "연산자 정의" 행 신설, 업그레이드 행 1.2, "표현은 YAML과 그래프 둘" 명시. 금지 절의 DSL 문장 유지 |
| ADR 2026-09-04 | D8 대상 사용자·완료 정의 개정 링크 |
| 1.1 spec | 머리말에 이 문서로의 확장 링크 |
| 로드맵 | 완료 정의 원문 복귀, M8 "custom formula editor"는 여전히 non-goal |
| `docs/manual/strategy-workbench/README.md`, `README.md`, `frontend/README.md`, `backend/FACTORS.md` | 1.2 문법, 실행 설정, 그래프 화면, 예시 전략 5개(튜토리얼) |
| fixture | `quality_momentum.yaml` 1.2, `.v1_1.yaml`(업그레이드 golden), `ideas/*.yaml` 5개(완료 정의) |

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
| P2 | backend schema 1.2(실행 설정 분리, 추가 필드, 단일 게이트, 업그레이더) | P2-01 ~ P2-05 |
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
- P2는 backend만 바꾼다. P2-05(업그레이더) merge 전에는 실 DB에 1.2 revision을 저장하지 않는다.
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
