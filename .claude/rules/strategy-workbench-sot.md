---
paths:
  - "backend/**"
  - "frontend/**"
  - "docs/superpowers/specs/*strategy-workbench*"
  - "docs/superpowers/specs/2026-09-04-*-adr.md"
  - "docs/planning/strategy-workbench-yaml-ui/**"
---

# Strategy Workbench의 사실은 한 곳만 소유한다

## 정본 대장

| 사실 | 유일한 owner | 나머지 레이어 |
|---|---|---|
| 원천 값·공개 시점·coverage | Equity DB view + `dataset_profile` | `EquityDataPort`와 `RawObservationPort` 두 포트로 조회하며, 같은 셀에 같은 값·공개일을 답하는 것이 어댑터 계약이다 |
| Equity 연결 계약 | backend application outbound port | adapter가 구현 |
| 팩터 정의·방향·단위·입력 요구 | backend Factor Registry | UI는 catalog 표시 |
| 팩터 **값** | `FactorGraph` 평가 (`domain/factor`) | 계산자는 `application/portfolio_design/_service.py` 하나뿐, 어댑터는 원천 필드만 답한다 |
| 팩터 값의 공개일 | 그 팩터 plan이 읽는 필드들의 `available_date` 최댓값 | 컴파일러 FUTURE_DATA 가드가 그대로 읽는다 |
| 리밸런싱 시점의 previous weight | `compile_target_tape`의 프레임 fold | 포트의 `previous_weight`는 첫 프레임 시드로만 쓰인다 |
| 전략 의미 | immutable, versioned `StrategySpec` | YAML/JSON source를 서버가 compile한다. Form 편집은 runtime schema × parse tree projection 위의 **source 트랜잭션**(`planSourceOperation` → 편집기 `replaceRange` 한 번)이며 별도 편집 모델·직렬화 경로를 두지 않는다. Graph는 P5-01 전까지 읽기 전용이고, 편집이 들어오면 같은 경로를 쓴다. JSON 문서는 Form에서 읽기 전용이다 |
| authoring schema 버전 | `domain/strategy/_models.py`의 `CURRENT_SCHEMA_VERSION`(`_hydrate.py`의 `SUPPORTED_SCHEMA_VERSIONS`가 파생), 은퇴 버전은 `domain/strategy/_upgrade.py`의 `LEGACY_SCHEMA_VERSION` | 모델 기본값·스키마·검증·어댑터·테스트는 이 상수를 읽는다. `"1.1"`/`"1.0"` 리터럴을 다시 적지 않는다. frontend도 같다: 동결/업그레이드 판정은 `requires_upgrade`와 compile 진단으로만 하고 은퇴 버전 문자열을 갖지 않는다. 버전 리터럴을 남겨야 하면(새 문서 템플릿) 그 값이 runtime schema `schema_version.const`와 같은지 단언하는 테스트를 같은 PR에 넣는다 |
| 1.0 → 1.1 문서 업그레이드 변환 | `domain/strategy/_upgrade.py`의 `UPGRADE_STEPS` | dict 경로(repository codec, legacy generated source)와 source 경로(`adapters/outbound/document_codec/_upgrade_source.py`)가 같은 step을 적용한다. 어댑터는 주석·순서 보존만 맡고, 두 경로가 다른 tree를 내면 application이 `strategy_document.upgrade_drift` 422로 거부한다. frontend는 변환 규칙을 알지 않고 응답 원문을 그대로 적용한다 |
| 필드 적용 조건(모드별로 읽히는 필드) | `domain/strategy/_constraints.py`의 `FIELD_APPLICABILITY` | 같은 행에서 validator가 `strategy.field.inapplicable` warning을, runtime schema가 `x-applicable-when`을, contract가 `FieldContract.applicable_when`을 낸다. 이미 blocking error가 소유한 관계는 `owned_by_error`로 표시하고 warning을 두 번 내지 않는다 |
| revision의 동결(업그레이드 필요) 여부 | `domain/strategy/_upgrade.py`의 `is_frozen_schema_version`을 `StrategyRevisionRecord.requires_upgrade`(`application/strategy_design/ports/outgoing/strategy_repository.py`)가 적용 | 목록·history·문서 응답·saved-reference 실행 거부가 이 property를 그대로 전달한다. 어댑터와 HTTP 계층이 `schema_version`을 다시 비교해 동결을 판정하지 않는다 |
| 파라미터 공간 | `SearchSpec` | trial은 해소된 값만 참조 |
| 주문·체결·포트폴리오 mutable state | Persistent Rust Engine | Python/API는 명령·조회 |
| 지표 공식·방향·단위 | backend Metric Registry | UI는 raw metric 표시·포맷 |
| 실행 재현성 | immutable Run Manifest | 결과 화면이 그대로 노출 |
| 실험·trial 상태 | Experiment Repository | UI는 query cache로 구독 |
| 후보 선택 | 명시적인 사용자 selection record | composite score는 view일 뿐 |
| 미저장 편집 상태·그래프 좌표 | frontend feature/local UI state | 서버 정본으로 승격 금지 |
| 저장된 authoring source 텍스트·`source_hash` | strategy revision envelope (`source`, `source_hash`) | 서버는 exact text를 그대로 보관, UI는 표시·편집 시작점으로만 사용 |
| 편집기 source 트랜잭션(범위 계산·들여쓰기 폭·EOL·빈 컨테이너 표기) | `features/edit-strategy/model/source-transactions.ts`의 `planSourceOperation` | 범위는 `shared/lib/yaml12`의 `parseSource`가 낸 `valueRanges`/`keyRanges` offset을 그대로 쓰고 CST를 다시 걷지 않는다. 모든 연산은 완성된 다음 원문을 `parseSource`로 preflight하고 tree가 `applyToTree` 결과와 같을 때만 성공을 반환한다. 스니펫·Form·Graph는 이 함수에 연산을 넘기기만 하고 fragment 문자열을 직접 조립하지 않는다. source 트랜잭션의 편집기 적용(`replaceRange` 한 번·feedback scope)은 `use-source-transactions.ts`의 `useSourceTransactions` 하나가 하고, 스니펫 훅은 그 `run`을 쓴다(backend 업그레이드 응답 적용 `use-upgrade-document.ts`는 아래 '금지' 절이 따로 허용한 전체 범위 교체다). Form·Graph는 `enabled`가 참일 때만 연산을 넘긴다(계획은 편집기 live 텍스트로 세우므로 stale pointer는 preflight가 잡지 못한다) |
| runtime schema 노드의 필드 표시 사실(type·enum·기본값·범위·format·단위·카탈로그·참조·적용 조건) | `features/edit-strategy/model/schema-navigator.ts`의 `schemaFacts`·`referenceCandidates` | Contract Inspector·Form projection이 같은 함수를 호출하고 typed contract 행이 있으면 그 행이 우선한다. authoring·구조 마커(`x-authoring-*`는 스니펫·목록 항목 이름, `x-defines`는 참조 정의 배열 탐색)는 스니펫·projection·outline이 직접 읽는 별개 계약이다. 스니펫 materialize(`materializeSchemaValue`)는 값 생성을 위해 `const`·`enum`·범위·`default`를 직접 읽으며 P4-03에서 `schema-navigator.ts`로 옮겼다. 편집기 완성(`schema-assist.ts`)도 `x-catalog`/`x-reference`를 `schemaFacts`로 읽는다(P4-03, Phase 3 감사 R5) |
| YAML 1.2 허용/거부 집합 | `backend/tests/fixtures/strategy_documents/yaml12/manifest.json` | backend codec test와 frontend `yaml` cross-runtime test가 같은 manifest를 실행 |
| 실행 차단(blocking) 판정 | backend compile diagnostics의 error severity | frontend syntax marker는 advisory, 실행 가능 여부를 판단하지 않음 |
| authoring 진단 코드 | `strategy.*`는 domain 코드 레지스트리, `structure.*`는 domain hydrate, codec 코드(`document.*`/`yaml.*`/`<format>.syntax`)는 `ports/outgoing/document_codec.py` | frontend는 코드 → 마커 매핑과 422 코드 번역(`upgrade.error.<code>`·`backtest.error.<code>`)만 한다. compile 진단의 `message`는 backend가 한글 문장으로 완성해 보내고 Problems panel은 그대로 보여준다(frontend가 다시 조립·번역하지 않음, Phase 2 감사 DEFECT-P2X-005(b)); 코드를 새로 만들지 않는다 |
| URL 선택 상태(view/path/date/security) | TanStack Router search (`validateSearch`) | widget은 읽기만, 기본값은 URL에 쓰지 않음 |

## 금지

- frontend에 팩터 공식, 지표 공식, 전략 validation 규칙, 서버 status transition을 복제하지 않는다.
- 저장된 서버 응답을 Redux/Zustand 같은 client store에 한 벌 더 두지 않는다.
- 파생 가능한 값, 실행 plan, generated code, composite score를 원본으로 저장하지 않는다.
- StrategySpec을 화면별 별도 포맷으로 만들지 않는다. YAML/JSON source ↔ StrategySpec ↔
  JSON/Form/Graph/Diff projection은 같은 `spec_hash`로 lossless round-trip해야 한다. 표현식 문자열
  DSL과 단위 literal은 v1 범위가 아니다 (`docs/superpowers/specs/2026-09-04-strategy-authoring-contract-adr.md`).
- canonical `spec_hash`와 semantic validation은 backend 응답만 신뢰한다. frontend가 만든 spec이나
  hash를 표시·실행에 쓰지 않는다.
- 캐시를 SoT로 취급하지 않는다. 캐시 키는 입력 spec hash, 데이터 snapshot, registry/engine
  version, cost model, seed를 모두 포함한다.
- 실패·pruned trial을 결과에서 지우지 않는다. 전체 trial 수와 실패 이유는 audit 대상이다.
- frontend에 1.0 → 1.1 업그레이드 규칙과 필드 적용 조건표를 복제하지 않는다. 업그레이드는
  `POST /api/v1/strategy-documents/upgrade`가 돌려준 원문을 편집기 범위 교체 한 번으로 적용한다.

같은 사실이 두 위치에서 변경되어야 한다면 구현을 멈추고 owner를 한 곳으로 합친다.
