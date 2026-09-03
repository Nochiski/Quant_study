---
paths:
  - "backend/**"
  - "frontend/**"
  - "docs/superpowers/specs/*strategy-workbench*"
---

# Strategy Workbench의 사실은 한 곳만 소유한다

## 정본 대장

| 사실 | 유일한 owner | 나머지 레이어 |
|---|---|---|
| 원천 값·공개 시점·coverage | Equity DB view + `dataset_profile` | port로 조회 |
| Equity 연결 계약 | backend application outbound port | adapter가 구현 |
| 팩터 정의·방향·단위·입력 요구 | backend Factor Registry | UI는 catalog 표시 |
| 전략 의미 | immutable, versioned `StrategySpec` | Quick/Advanced UI가 같은 draft 편집 |
| 파라미터 공간 | `SearchSpec` | trial은 해소된 값만 참조 |
| 주문·체결·포트폴리오 mutable state | Persistent Rust Engine | Python/API는 명령·조회 |
| 지표 공식·방향·단위 | backend Metric Registry | UI는 raw metric 표시·포맷 |
| 실행 재현성 | immutable Run Manifest | 결과 화면이 그대로 노출 |
| 실험·trial 상태 | Experiment Repository | UI는 query cache로 구독 |
| 후보 선택 | 명시적인 사용자 selection record | composite score는 view일 뿐 |
| 미저장 편집 상태·그래프 좌표 | frontend feature/local UI state | 서버 정본으로 승격 금지 |

## 금지

- frontend에 팩터 공식, 지표 공식, 전략 validation 규칙, 서버 status transition을 복제하지 않는다.
- 저장된 서버 응답을 Redux/Zustand 같은 client store에 한 벌 더 두지 않는다.
- 파생 가능한 값, 실행 plan, generated code, composite score를 원본으로 저장하지 않는다.
- StrategySpec을 화면별 별도 포맷으로 만들지 않는다. Quick Builder와 Advanced Graph는 동일한
  AST/DAG를 lossless round-trip해야 한다.
- 캐시를 SoT로 취급하지 않는다. 캐시 키는 입력 spec hash, 데이터 snapshot, registry/engine
  version, cost model, seed를 모두 포함한다.
- 실패·pruned trial을 결과에서 지우지 않는다. 전체 trial 수와 실패 이유는 audit 대상이다.

같은 사실이 두 위치에서 변경되어야 한다면 구현을 멈추고 owner를 한 곳으로 합친다.
