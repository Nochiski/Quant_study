# ADR: Strategy Authoring Contract (P0-01)

> 작성: 2026-09-04
>
> 상태: Accepted
>
> Initiative: [YAML Strategy Workbench](../../planning/strategy-workbench-yaml-ui/README.md) —
> 범위는 [WORKFLOW.md](../../planning/strategy-workbench-yaml-ui/WORKFLOW.md), PR 진행은
> [PLAN.md](../../planning/strategy-workbench-yaml-ui/PLAN.md)
>
> 상위 제품 milestone: [Strategy Workbench 로드맵](./2026-09-03-strategy-workbench-roadmap.md)

## 1. 맥락

M1~M5까지 Strategy Workbench는 Quick Builder와 Advanced Graph라는 두 no-code 편집기가 같은
`StrategySpec` draft를 편집하는 구조로 만들어졌다. 전문 트레이더 사용자는 폼과 그래프보다
문서를 직접 쓰고, 계약을 확인하고, 실제 계산 중간값을 추적하는 IDE형 작업 흐름을 원한다.

이 ADR은 그 전환에서 구현 도중 바뀌면 안 되는 계약을 고정한다. editor·parser·router 같은
라이브러리 선택은 P0-02, P0-03, P0-04가 별도 ADR로 결정한다.

## 2. 결정

### D1. 실행 의미의 정본은 `StrategySpec`, YAML/JSON은 authoring source다

- 실행 의미(SoT)는 기존 backend의 immutable/versioned `StrategySpec`
  (`backend/src/strategy_workbench/domain/strategy/_models.py`)이다. 이 ADR은 모델을 바꾸지 않는다.
- YAML과 JSON은 사람이 작성하는 source다. 저장·실행 시 서버가 source를 parse → typed
  `StrategySpec`으로 hydrate → semantic validation → canonical JSON → `spec_hash` 순서로 다시
  compile한다. 프론트가 계산한 spec이나 hash는 표시용으로도 신뢰하지 않는다.
- untyped dict를 typed 모델 없이 canonical JSON으로 직렬화하는 경로는 만들지 않는다. typed
  `float`/`int`/`date`/enum 필드에서는 `1`/`1.0`, `15`/`15.0`, 날짜 문자열, enum 문자열이 typed
  hydrate로 정규화되어 같은 hash가 나온다.
- `ParameterValue`(`float | int | str | bool`) union은 입력 타입이 보존되어 `1`과 `1.0`이 다른 hash를
  낸다. P1-01이 선언된 `kind`를 기준으로 coercion 규칙을 정하고 choice/int/float parameter
  fixture로 고정한다. 그 전까지 이 union은 "같은 의미 = 같은 hash" 보장 범위 밖이다.

### D2. v1 authoring 문법은 canonical verbose YAML/JSON이다

- source의 key 이름과 값은 canonical payload와 1:1이다. `max_name_weight: 0.05`, `fee_bps: 15.0`,
  `rebalance: monthly`, `graph.nodes[]`를 그대로 쓴다.
- 표현식 문자열 DSL(`rank(neutralize(...))`)과 단위 literal(`5%`, `15bps`, `month_end`)은 v1
  non-goal이다. 사람이 읽기 좋은 단위는 Contract Inspector가 backend contract metadata로
  표시한다.
- DSL은 로드맵 M8 "custom formula editor"에 해당하는 별도 initiative다. 도입하려면 parser,
  문자열 내부 source map, DSL→FactorGraph compiler, pretty-printer, round-trip 검증이 먼저 있어야
  한다.
- 기준 문서는 `docs/planning/strategy-workbench-yaml-ui/WORKFLOW.md` 2.2의 완전한 YAML 예시이며,
  `backend/tests/fixtures/strategy_documents/quality_momentum.yaml`로 등록되어 P1-03 compile
  golden fixture가 된다.

### D3. Revision envelope와 두 종류의 hash

```text
StrategyRevision
├─ strategy_id / revision              ← envelope가 소유, source 문서 안에 없음
├─ schema_version                      ← source 최상위 key
├─ immutable StrategySpec
├─ spec_hash                           ← canonical JSON sha256 (기존 알고리즘 유지)
├─ original source
│  ├─ format: yaml | json
│  ├─ exact text (UTF-8 그대로)
│  └─ source_hash                      ← exact text sha256
└─ created_at / change_note
```

- `spec_hash`는 기존 `strategy_spec_hash`를 그대로 쓴다. identity를 제외하고 `schema_version`을
  최상위로 올린 canonical payload를 `sort_keys`, 최소 separator, `allow_nan=False`로 직렬화한
  sha256이다. 이 알고리즘은 바꾸지 않는다.
- 주석, 공백, key order, YAML/JSON 형식 차이는 `source_hash`에만 반영된다. 같은 의미의 YAML과
  JSON은 같은 `spec_hash`를 가진다.
- `title`, `description`, `label`은 전략 의미의 일부이므로 `spec_hash`에 포함된다. identity가
  아니기 때문이다.
- legacy revision(source 없이 저장된 M1~M5 전략)은 `source=None`으로 호환하고, 조회 시 canonical
  payload에서 생성한 source와 provenance를 돌려준다.

### D4. Save·Run·Debug 허용 불변식

현재 source가 다음을 모두 만족할 때만 Save, Backtest, Debug를 허용한다.

```text
sourceVersion == compiledVersion
AND syntax blocking error == 0
AND structural blocking error == 0
AND semantic blocking error == 0
```

- blocking error의 정의: `syntax`는 parse 실패, `structural`은 typed hydrate 실패(필수 필드 누락,
  타입 불일치, 지원하지 않는 `schema_version`, 그리고 **모든 depth의 unknown key**), `semantic`은
  `validate_strategy` error다. unknown key는 오타(`max_name_wieght`)가 default 값으로 조용히
  대체되는 것을 막기 위해 JSON Pointer 위치와 함께 fail-closed한다. 현행 pydantic 경로는 extra key를
  무시하므로 P1-01 hydrate가 이 규칙을 구현한다.
- `lastValidSpec`은 stale 상태를 명시한 조회 전용이다. invalid 또는 stale source에서 과거 spec을
  몰래 실행하지 않는다.
- Backtest는 dirty가 아니고 base revision과 `expected_spec_hash`가 일치할 때만 saved revision
  reference(`strategy_id + revision + expected_spec_hash`)를 쓴다. 그 외의 valid/current 문서는
  inline draft로 실행하고 run manifest에 source/spec provenance를 남긴다 (P1-09, P3-05).
- 미래 live deployment는 saved revision reference만 허용한다. inline draft는 backtest 전용이다.

### D5. Form과 Graph는 v1에서 read-only projection이다

- JSON, Form, Graph, Diff view는 현재 valid `StrategySpec`을 읽는 projection이며 새 편집
  모델이 아니다. invalid source에서는 last valid 값을 `stale` badge와 함께 보여준다.
- projection에서 source로 되돌아가는 편집(Form 전체 reserialize 등)은 YAML 주석과 순서를
  잃으므로 v1에서 제공하지 않는다. Graph 직접 편집은 후속 범위다.
- 검증 대상은 `source ↔ StrategySpec ↔ projection` round-trip이다. source를 hydrate한 spec과
  projection이 보여주는 spec이 같은 `spec_hash`를 가져야 한다.

### D6. YAML ↔ JSON 변환은 명시적 사용자 동작이다

- 문서 하나의 format은 YAML 또는 JSON 중 하나다. 자동 변환하지 않는다.
- 변환은 `포맷` 명령으로만 수행하고, 변환 전후 `spec_hash`가 같아야 완료된다. YAML 주석은
  JSON으로 갈 때 사라지므로 변환 전에 사용자에게 알린다.

### D7. Credential은 source에 넣지 않는다

- broker/API credential, 토큰, 절대 경로는 source와 revision에 저장하지 않는다. 필요한 경우
  secret reference(이름)만 허용하며 해석은 운영 계층이 한다.
- compile은 알려진 credential 패턴을 capability diagnostic으로 거부한다 (P1-03 이후).

### D8. 제품 방향: YAML-first, no-code는 범위 조정

- 전략 정의의 primary authoring은 YAML/JSON이다. 대상 사용자는 전문 트레이더다.
- 로드맵의 완료 정의 "코드를 몰라도 대부분의 cross-sectional 전략을 만들 수 있다"는 다음으로
  조정한다: "전략 정의는 문서로 작성하되, parameter search와 실험 실행은 source를 다시 편집하지
  않고 UI에서 수행할 수 있다." Form/Graph projection과 snippet, completion, Contract Inspector가
  비개발자의 진입 장벽을 낮추는 역할을 맡는다.
- Quick/Advanced 편집기는 legacy route로 유지된다. 삭제 조건은 D9.

### D9. Quick/Advanced deprecation 정책

삭제는 P6-06에서 다음 조건이 모두 충족될 때만 별도 cleanup commit으로 수행한다.

1. 로드맵, `.claude/rules`, README, i18n 문구가 YAML-first로 갱신되어 있다 (이 PR에서 문서·규칙,
   P3-05 cutover에서 i18n 문구).
2. YAML route에서 new → edit → validate → save → reload → backtest, invalid → marker → fix,
   autosave recovery, 409 conflict, revision diff, factor trace E2E가 통과한다.
3. legacy로 저장된 모든 revision(`source=None`)이 YAML route에서 열리고 같은 `spec_hash`로
   재저장된다.
4. 로드맵 M6 parameter search UI가 YAML route에 연결되어 있다. 연결 전에는 Quick/Advanced를 지우지
   않는다.

조건이 하나라도 미충족이면 legacy route를 유지하고 삭제를 별도 initiative로 넘긴다.

### D10. 로드맵과 tracker의 관계

- 로드맵(`2026-09-03-strategy-workbench-roadmap.md`)이 product milestone SoT다. 이 initiative는
  로드맵 M6 이전에 끼어드는 authoring/UI 전환이며, M6~M10의 순서는 바꾸지 않는다.
- `PLAN.md`가 이 initiative의 PR delivery SoT다. 로드맵은 PR 단위 상태를 복제하지 않고
  `PLAN.md`를 링크한다.
- 로드맵 M8 항목 중 revision history/diff, autosave/recovery, revision conflict, keyboard
  navigation은 이 initiative(P1-08, P3-06, P3-07, P4-08, P6-02, P6-03)가 먼저 제공한다. 로드맵
  M8 체크박스는 해당 PR merge 시 갱신한다.
- 로드맵 M6 parameter search는 이 initiative의 Phase 3(YAML MVP) 이후에 YAML route 위에서
  진행한다. Phase 1.5 correctness gate는 M6보다 먼저 끝나야 한다.

## 3. 규칙·문서 갱신

이 PR이 함께 바꾸는 것:

| 위치 | 변경 |
|---|---|
| `.claude/rules/strategy-workbench-sot.md` | 전략 의미 row와 금지 항목을 source/projection 표현으로 갱신 |
| `.claude/rules/frontend-testing.md` | round-trip property test 대상을 source ↔ StrategySpec ↔ projection으로 갱신 |
| `README.md`, `frontend/README.md`, `backend/FACTORS.md` | no-code 표현을 YAML-first + legacy no-code 유지로 갱신 |
| 로드맵 | 결론·시스템 한 컷·7.1·9.1·M8 gate·완료 정의 갱신, 16절에서 이 tracker 링크 |

i18n 문구(`builder.subtitle` 등)는 legacy 편집기가 아직 기본 화면이므로 이 PR에서 바꾸지 않는다.
P3-05 YAML route cutover에서 ko/en을 함께 갱신한다 (WORKFLOW P3-05 acceptance에 반영).

알려진 잔존 표현: HTTP API 설명 문자열(`adapters/inbound/http_api/_app.py`의 "No-code factor
strategy design")과 그로부터 생성된 `backend/openapi.json`은 API 코드 변경이 non-goal이므로 P1-03에서
갱신한다. 로드맵 M3~M5 완료 기록의 Quick/Advanced 표현은 이력이므로 유지한다.

## 4. Acceptance fixture

`backend/tests/fixtures/strategy_documents/`:

| 파일 | 역할 |
|---|---|
| `quality_momentum.yaml` | WORKFLOW 2.2의 완전한 verbose YAML. P1-03 compile golden |
| `quality_momentum.json` | 같은 의미의 JSON. key order 다름, 일부 default 명시, `fee_bps: 15` int |
| `quality_momentum.legacy.json` | identity를 문서 안에 가진 현행 API payload |
| `quality_momentum.invalid.yaml` | 구문 오류. StrategySpec이 되면 안 된다 |
| `quality_momentum.unknown_key.yaml` | `risk.max_name_wieght` 오타. structural fail-closed 대상 (P1-01 구현 전까지 strict xfail) |

`backend/tests/contract/test_strategy_authoring_fixtures.py`가 검증하는 것:

- 세 fixture의 `spec_hash`가 같다 (`9eb6872a…98fe`).
- 2.2 예시가 identity 주입 후 hydrate된다.
- int/float literal이 같은 spec이 된다.
- identity 외 canonical round-trip이 보존된다.
- `template()` hash golden이 유지된다 (알고리즘 불변).
- 구문 오류·필수 필드 누락은 fail-closed다.
- unknown key는 fail-closed다. 현행 pydantic 경로는 이를 무시하므로 `strict xfail`로 계약을 고정하고
  P1-01이 xfail을 제거한다.

YAML loader는 P1-02 codec 전까지 `yaml.safe_load`를 임시로 쓴다. fixture는 YAML 1.1 implicit
typing을 피하도록 날짜·버전을 quoted string으로 적으며, P0-03이 cross-runtime fixture로 대체한다.

## 5. Non-goals

기능:

- 표현식 문자열 DSL, 단위 literal, YAML anchor/alias/merge key/custom tag
- Form/Graph에서 source로의 편집
- 자동 merge, 다중 사용자 실시간 편집
- live trading, deployment, order routing (WORKFLOW 15절 경계만 유지)

비기능:

- 이 PR은 editor, parser, router 라이브러리를 설치하지 않는다.
- 이 PR은 API, domain 모델, UI 코드를 바꾸지 않는다.
- P1-01 이전에는 hydrate 헬퍼가 테스트 안에만 존재한다.

## 6. 대안

- **표현식 DSL을 v1에 포함**: 시안이 예뻐지지만 parser·source map·compiler·pretty-printer가
  Phase 1에 PR 3~4개 추가되고 "별도 authoring 모델 없음" 원칙과 충돌한다. M8로 미룬다.
- **Form을 편집 가능하게 유지**: YAML 주석·순서 보존과 undo history 통합이 필요해 source SoT가
  둘이 된다. projection으로 제한한다.
- **Quick/Advanced 즉시 삭제**: legacy revision 호환과 M6 UI 연결 전에는 사용자 흐름이 끊긴다.
  조건부 삭제(D9)로 둔다.

## 7. Rollback

이 PR은 문서·규칙·fixture·테스트만 추가한다. 되돌리려면 PR을 revert하면 되고, 코드 동작은
바뀌지 않는다.
