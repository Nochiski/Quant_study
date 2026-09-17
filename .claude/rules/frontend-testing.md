---
paths:
  - "frontend/src/**/__tests__/**"
  - "frontend/src/**/*.test.ts"
  - "frontend/src/**/*.test.tsx"
  - "frontend/e2e/**"
---

# 프론트 테스트는 사용자 동작과 wire 경계를 검증한다

이 규칙은 `michelo.frontend`의 `testing.md`와 `e2e-impact.md`를 차용한다.

- 사용자 대면 동작이 같을 때 깨지는 내부 구현 테스트를 만들지 않는다. 내부 state, private
  method, 자식 컴포넌트, CSS class를 직접 단언하지 않는다.
- query 우선순위는 role > label > text > test id다. role을 줄 수 있는 요소에 test id로
  우회하면 컴포넌트 접근성을 먼저 고친다.
- 상호작용이나 조건 분기가 없는 단순 존재 테스트는 만들지 않는다.
- API 모듈 자체를 mock하지 않고 MSW로 wire 요청·응답·오류를 검증한다.
- YAML/JSON source ↔ StrategySpec ↔ projection(JSON/Form/Graph/Diff) round-trip은
  property/generative test로 lossless를 검증한다. 삭제된 Quick/Advanced 편집 모델을 테스트나
  fixture에서 다시 만들지 않는다.
- YAML 1.2 허용/거부 집합의 owner는 `backend/tests/fixtures/strategy_documents/yaml12/manifest.json`
  하나다. frontend `yaml` cross-runtime test와 backend codec test가 같은 manifest를 실행하며, 한쪽
  fixture만 추가·수정하지 않는다.
- backend 모델에서 생성되는 공유 fixture(`backend/tests/fixtures/strategy_documents/runtime-schema.json`,
  `quality_momentum.*`)의 owner는 backend다. frontend 테스트는 `readBackendFixture`로 읽기만 하고
  복사본이나 손으로 쓴 축약본을 만들지 않는다. 모델이 바뀌면 backend가
  `uv run python tools/export_runtime_schema.py`로 재생성한 결과를 그대로 쓴다.
- schema 1.0 동결 revision row는 공개 API로 만들 수 없다(1.0 인코더 없음). e2e seeding의 owner는
  `backend/tests/frozen_revision_rows.py`의 CLI 하나이며, Playwright는 `STRATEGY_WORKBENCH_E2E_DB`
  (격리 SQLite 경로)와 `STRATEGY_WORKBENCH_E2E_SEED_SUFFIX`(시도별 고유 전략 id)를 환경 변수로
  넘겨 부른다. frontend에 row를 만드는 스크립트나 SQL을 따로 두지 않는다.
- backend가 발행하는 문구 키(`description_key`, `x-description-key`)는 backend가 owner다. frontend는
  번역만 소유하되, runtime schema fixture의 키 집합이 `messages.ko`에 모두 있는지 단언하는
  테스트로 누락을 막는다. `tOptional`은 없는 키를 조용히 삼킨다.
- UI/API/i18n 변경 시 관련 Playwright spec의 test id, 문구, API path 영향을 검색하고 함께
  수정한다.
- mock 성공 경로뿐 아니라 PIT 경고, invalid spec, failed/pruned trial, cancellation, 부분 결과
  표시를 테스트한다.
