---
paths:
  - "frontend/src/**/__tests__/**"
  - "frontend/src/**/*.test.ts"
  - "frontend/src/**/*.test.tsx"
  - "frontend/e2e/**"
---

# 프론트 테스트는 사용자 동작과 wire 경계를 검증한다

이 규칙은 `michelo.frontend`의 `testing.md`와 `e2e-impact.md`를 차용한다. 쓰나·지우나의 판단 기준
(트리거가 있을 때만 쓴다, 깨뜨릴 현실적인 버그를 댈 수 있어야 한다, 지우는 것도 개선이다, 커버리지는 기준이 아니다)은
`testing.md`와 같고, 아래는 그것을 프론트 경로에 적용한 것이다.

## 언제 쓰나

- **쓴다**: `shared/lib/**`·`entities/*/model/**`·`features/*/model/**`의 분기·계산·변환(문서 source ↔ spec ↔ projection,
  wire 응답 → 화면 모델, 폼 검증), 사용자 상호작용으로 화면 상태가 바뀌는 UI(입력 → 요청 → 결과·오류 표시), 버그 수정의 재현 테스트,
  backend 계약 키(문구 키·오류 코드)의 누락 가드
- **쓰지 않는다**: 분기 없는 표시 컴포넌트, props 전달·레이아웃·스타일만 바꾼 변경, `shared/api/generated/**` 생성물,
  re-export·상수·i18n 문구 수정, 라우트 등록 배선. 생성 SDK 타입과 `tsc`가 이미 잡는 것(필드 이름·필수 여부)은 테스트로 다시 보지 않는다
- 같은 사용자 동작을 단위 테스트와 Playwright e2e에서 겹쳐 검증하지 않는다. e2e는 스토리 경로와 렌더 회귀를, vitest는 분기와 wire 경계를 맡는다

## 언제 지우나

- MSW 응답이나 mock이 돌려준 값을 그대로 단언하고 대상 코드는 거의 거치지 않는 테스트
- 렌더만 하고 "보인다"만 단언하는 존재 테스트(상호작용·조건 분기 없음)
- 내부 state·CSS class·자식 컴포넌트 구조에 결합해 동작이 같아도 리팩터링하면 깨지는 테스트
- 같은 입력 조건을 같거나 더 엄격한 단언으로 보는 테스트를 댈 수 있는 중복 테스트
- 실패 중인 테스트와 계약 가드(문구 키 집합, yaml12 manifest, 공유 fixture round-trip)는 지우지 않는다 — `testing.md` §언제 지우나와 같다

- 사용자 대면 동작이 같을 때 깨지는 내부 구현 테스트를 만들지 않는다. 내부 state, private
  method, 자식 컴포넌트, CSS class를 직접 단언하지 않는다.
- query 우선순위는 role > label > text > test id다. role을 줄 수 있는 요소에 test id로
  우회하면 컴포넌트 접근성을 먼저 고친다.
- 상호작용이나 조건 분기가 없는 단순 존재 테스트는 만들지 않는다.
- API 모듈 자체를 mock하지 않고 MSW로 wire 요청·응답·오류를 검증한다.
- YAML/JSON source ↔ StrategySpec ↔ projection(JSON/Form/Graph/Diff) round-trip은
  property/generative test로 lossless를 검증한다. GUI 트랜잭션(Form/Graph 연산 → source → spec)은
  `source-transactions.property.test.ts`(임의 문서·연산에서 tree 동등·범위 밖 바이트·주석 보존)와 route 테스트
  (Form 값 변경 → source 문자열에 주석·순서 유지 → compile)로 검증한다. 삭제된 Quick/Advanced 편집 모델을 테스트나
  fixture에서 다시 만들지 않는다.
- YAML 1.2 허용/거부 집합의 owner는 `backend/tests/fixtures/strategy_documents/yaml12/manifest.json`
  하나다. frontend `yaml` cross-runtime test와 backend codec test가 같은 manifest를 실행하며, 한쪽
  fixture만 추가·수정하지 않는다.
- backend 모델에서 생성되는 공유 fixture(`backend/tests/fixtures/strategy_documents/runtime-schema.json`,
  `quality_momentum.*`)의 owner는 backend다. frontend 테스트는 `readBackendFixture`로 읽기만 하고
  복사본이나 손으로 쓴 축약본을 만들지 않는다. 모델이 바뀌면 backend가
  `uv run python tools/export_runtime_schema.py`로 재생성한 결과를 그대로 쓴다.
- 은퇴 버전(1.0·1.1) revision row는 공개 API로 만들 수 없다(저장은 현재 버전 문서만 받는다).
  seeding의 owner는 `backend/tests/frozen_revision_rows.py` 하나다 — backend 테스트는 그 함수
  (`seed_frozen_rows`·`seed_retired_1_1_row`·`seed_retired_1_1_document_row`)를, e2e와 매뉴얼 촬영 스크립트는 그 CLI(기본은 1.0 row,
  `STRATEGY_WORKBENCH_E2E_SEED_SCHEMA=1.1`이면 원문이 있는 1.1 row)를 부른다. Playwright는 `STRATEGY_WORKBENCH_E2E_DB`
  (격리 SQLite 경로)와 `STRATEGY_WORKBENCH_E2E_SEED_SUFFIX`(시도별 고유 전략 id)를 환경 변수로
  넘겨 부른다. frontend에 row를 만드는 스크립트나 SQL을 따로 두지 않는다.
- backend가 발행하는 문구 키(`description_key`, `x-description-key`)는 backend가 owner다. frontend는
  번역만 소유하되, runtime schema fixture의 키 집합이 `messages.ko`에 모두 있는지 단언하는
  테스트로 누락을 막는다. `tOptional`은 없는 키를 조용히 삼킨다.
- UI/API/i18n 변경 시 관련 Playwright spec의 test id, 문구, API path 영향을 검색하고 함께
  수정한다.
- 유저 스토리를 지키는 e2e에는 `@story`와 `@US-*` 태그를 붙인다. 스토리 하나를 위한 새 e2e는
  `frontend/e2e/stories/`에 둔다. 의무와 절차는 `.claude/rules/user-story-harness.md`가 소유한다.
- mock 성공 경로뿐 아니라 PIT 경고, invalid spec, failed/pruned trial, cancellation, 부분 결과
  표시를 테스트한다.
