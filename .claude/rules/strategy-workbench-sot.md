---
paths:
  - "backend/**"
  - "frontend/**"
  - "docs/superpowers/specs/*strategy-workbench*"
  - "docs/superpowers/specs/2026-09-04-*-adr.md"
  - "docs/superpowers/specs/2026-09-20-strategy-language-2-0-and-pipeline-canvas-design.md"
  - "docs/planning/strategy-workbench-yaml-ui/**"
  - "docs/planning/strategy-gui-editing/**"
  - "docs/planning/strategy-language-2-0/**"
  - "docs/planning/ai-assistant/**"
  - "docs/superpowers/specs/2026-09-20-ai-assistant-design.md"
  - "docs/superpowers/specs/2026-09-27-ai-backtest-result-explain.md"
  - "docs/planning/validation-lab/**"
  - "docs/planning/research-to-trading/**"
  - "docs/superpowers/specs/2026-09-29-validation-lab-design.md"
  - "database/src/equity/**"
---

# Strategy Workbench의 사실은 한 곳만 소유한다

## 소유는 코드로 드러낸다

- 사실마다 owner 모듈(함수·상수·타입)이 하나다. owner 모듈의 docstring이 자기가 무엇의 owner인지 말한다.
- 소비자는 owner를 그 노드의 facade·포트(frontend는 slice `index.ts`)를 거쳐 import·호출해 쓰고 값·규칙을
  옮겨 적지 않는다.
- 레이어 방향이나 런타임 때문에 import할 수 없으면 owner가 계약(포트·스키마·응답)으로 내보내고 소비자는 그
  계약을 읽는다. 그것도 안 되는 자리에서만 주석으로 owner 모듈을 가리킨다.
- owner 위치를 나열하는 대장·목록 문서를 따로 두지 않는다. 규칙·README·주석도 owner 모듈을 직접 가리킨다.

## 금지

- frontend에 팩터 공식, 지표 공식, 전략 validation 규칙, 서버 status transition을 복제하지 않는다.
- 저장된 서버 응답을 Redux/Zustand 같은 client store에 한 벌 더 두지 않는다.
- 파생 가능한 값, 실행 plan, generated code, composite score를 원본으로 저장하지 않는다.
- StrategySpec을 화면별 별도 포맷으로 만들지 않는다. YAML source(JSON 원문은 바이트를 보존한 YAML 1.2 부분집합으로 편집) ↔ StrategySpec ↔
  그래프의 파이프라인·레시피·노드 표현 및 revision Diff은 같은 `spec_hash`로 lossless round-trip해야 한다. 표현식 문자열
  DSL과 단위 literal은 v1 범위가 아니다 (`docs/superpowers/specs/2026-09-04-strategy-authoring-contract-adr.md`).
- canonical `spec_hash`와 semantic validation은 backend 응답만 신뢰한다. frontend가 만든 spec이나
  hash를 표시·실행에 쓰지 않는다.
- 캐시를 SoT로 취급하지 않는다. 캐시 키는 입력 spec hash, **실행 설정 `environment_hash`**, **해소된
  파라미터 값**(실행 지문은 `run_spec.parameter_values`), 데이터 snapshot(필드 계약 판 포함),
  registry/engine version, cost model, seed를 모두 포함한다. frontend 추적 결과 캐시 키(`prepareStrategyTrace` 의
  `ownerKey`)는 요청에 싣는 값 전부(실행 설정 포함)로 만들고 요청 칸을 따로 골라 적지 않는다. 결측 정책은 실행 설정으로
  옮긴 뒤에도 팩터 실행 plan의 일부라 `plan_hash`에 남아 trace provenance가 서로 다른 평가를 같은 계획으로 표시하지 않게 한다.
- 실패·pruned trial을 결과에서 지우지 않는다. 전체 trial 수와 실패 이유는 audit 대상이다.
- frontend에 1.0 → 1.1 → 1.2 업그레이드 규칙과 필드 적용 조건표를 복제하지 않는다. 업그레이드는
  `POST /api/v1/strategy-documents/upgrade`가 돌려준 원문을 편집기 범위 교체 한 번으로 적용한다.
  AI 어시스턴트의 전략 제안도 같은 방식(편집기 전체 범위 교체 한 번, 문서가 바뀌었으면 적용 전 확인)으로만 적용한다.

같은 사실이 두 위치에서 변경되어야 한다면 구현을 멈추고 owner를 한 곳으로 합친다.
