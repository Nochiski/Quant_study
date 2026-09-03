# Strategy Workbench Frontend

비개발자도 전략을 만들 수 있는 no-code UI다. 저장되는 전략의 의미는 백엔드의 버전된
`StrategySpec`이 소유하며, 이 폴더는 편집 경험과 시각화만 소유한다.

## FSD 의존성 방향

```text
app -> pages -> widgets -> features -> entities -> shared
```

- 같은 레이어의 서로 다른 slice는 직접 import하지 않는다.
- 다른 slice가 쓰는 심볼은 해당 slice의 `index.ts` public API로만 가져온다.
- `entities`는 명사(`strategy`, `factor`, `experiment`, `metric`, `dataset`)다.
- `features`는 사용자 행동(`edit-strategy`, `configure-search`, `run-backtest`,
  `compare-candidates`, `inspect-run`)이다.
- 여러 entity/feature의 조합은 `widgets`나 `pages`가 한다.
- 서버 데이터는 query cache가 소유하고, 저장된 응답을 client store에 복제하지 않는다.
- frontend에서 지표·팩터·전략 의미를 다시 계산하지 않는다. 백엔드 응답을 표현한다.

초기 구현은 Quick Builder와 Advanced Graph가 같은 StrategySpec draft를 편집하게 한다.
그래프 좌표·패널 열림 상태 같은 UI metadata는 spec과 분리한다.
