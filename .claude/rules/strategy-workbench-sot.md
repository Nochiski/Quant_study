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
| 전략 의미 | immutable, versioned `StrategySpec` | YAML/JSON source를 서버가 compile, Form/Graph는 read-only projection (legacy Quick/Advanced는 migration 기간 유지) |
| 파라미터 공간 | `SearchSpec` | trial은 해소된 값만 참조 |
| 주문·체결·포트폴리오 mutable state | Persistent Rust Engine | Python/API는 명령·조회 |
| 지표 공식·방향·단위 | backend Metric Registry | UI는 raw metric 표시·포맷 |
| 실행 재현성 | immutable Run Manifest | 결과 화면이 그대로 노출 |
| 실험·trial 상태 | Experiment Repository | UI는 query cache로 구독 |
| 후보 선택 | 명시적인 사용자 selection record | composite score는 view일 뿐 |
| 미저장 편집 상태·그래프 좌표 | frontend feature/local UI state | 서버 정본으로 승격 금지 |
| 저장된 authoring source 텍스트·`source_hash` | strategy revision envelope (`source`, `source_hash`) | 서버는 exact text를 그대로 보관, UI는 표시·편집 시작점으로만 사용 |
| YAML 1.2 허용/거부 집합 | `backend/tests/fixtures/strategy_documents/yaml12/manifest.json` | backend codec test와 frontend `yaml` cross-runtime test가 같은 manifest를 실행 |
| 실행 차단(blocking) 판정 | backend compile diagnostics의 error severity | frontend syntax marker는 advisory, 실행 가능 여부를 판단하지 않음 |
| authoring 진단 코드 | `strategy.*`는 domain 코드 레지스트리, `structure.*`는 domain hydrate, codec 코드(`document.*`/`yaml.*`/`<format>.syntax`)는 `ports/outgoing/document_codec.py` | frontend는 코드 → 메시지·마커 매핑만, 코드를 새로 만들지 않는다 |
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

같은 사실이 두 위치에서 변경되어야 한다면 구현을 멈추고 owner를 한 곳으로 합친다.
