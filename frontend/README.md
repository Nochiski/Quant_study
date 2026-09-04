# Strategy Workbench Frontend

> 2026-09-04부터 전략 authoring은 verbose YAML/JSON source editor로 전환 중이다
> ([ADR](../docs/superpowers/specs/2026-09-04-strategy-authoring-contract-adr.md),
> [PLAN.md](../docs/planning/strategy-workbench-yaml-ui/PLAN.md)). 아래 M3~M5 설명의
> Quick Builder/Advanced Graph는 migration 기간 legacy route로 유지되는 현행 화면이다.

## M5 Backtest run · professional result

Builder 6단계는 현재 `StrategySpec`을 generated SDK로 single run에 제출한다. 사용자는 기본
Persistent Rust core와 패리티/debug용 Python reference, 초기 자본, benchmark, OOS 시작 구간을
설정할 수 있다. 실행 상태는 backend run lifecycle에서 polling하고 완료 결과는 query cache가 소유한다.

결과 화면은 backend `MetricRegistry` 응답을 그대로 사용해 equity/benchmark, drawdown,
monthly return, rolling Sharpe, gross/net exposure와 closed trade를 표시한다. raw metric table은
Full·IS·Validation·OOS·Window scope를 보존하고 `None`은 unavailable reason이 있는 N/A로,
실제 0은 숫자로 구분한다. manifest drawer에서 engine/data/tape hash와 데이터 경고를 확인할 수 있다.

## M4 Portfolio · Risk · Execution

Builder의 3~5단계는 동일한 `StrategySpec` draft에서 long/short와 N/percentile 선택,
4가지 weighting, eligibility·threshold·regime·liquidity·turnover, gross/net/name/sector 제약과
거래 비용을 편집한다. `entities/portfolio`는 generated SDK로 backend TargetTape preview를 읽어
세션별 score/rank/target/exclusion 이유와 engine capability를 표시한다. 화면은 target 비중을
재계산하지 않으며 T 종가→T+1 시가 실행 계약을 그대로 보여준다.

## M3 Factor editor

`entities/factor`는 generated OpenAPI 타입과 query를 통해 backend Factor Registry만 읽는다.
Quick Builder는 팩터 탐색·추가, 가중치, Lag/Rank/Z-score/Winsorize/Neutralize 체인과 IC 계열
진단을 제공한다. Advanced Graph는 동일한 `StrategySpec` graph를 typed input port, output
type/unit, minimum history, inline validation과 함께 표시한다. 두 모드에 별도 수식이나 DTO는 없다.

저장되는 전략의 의미는 백엔드의 버전된 `StrategySpec`이 소유하며, 이 폴더는 편집 경험과
시각화만 소유한다. YAML-first 전환 후 Form/Graph는 read-only projection이 된다.

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

legacy Quick Builder와 Advanced Graph는 같은 StrategySpec draft를 편집한다. 데이터 단계는 backend
catalog에서 필드의 단위·공개 시점·권장 lag·coverage·근거를 읽고, 실제 0·원천 생략 0·결측·
미수집·coverage gap을 PIT panel에서 별도 상태로 표시한다. 그래프 좌표·패널 열림 상태 같은
UI metadata는 spec과 분리한다.

## 개발

Node 22.18 이상을 기준으로 한다. backend가 실행 중일 때 Builder는
`http://localhost:8000`의 Strategy template, validate, revision, Equity mock API를 호출한다.

```powershell
cd frontend
npm ci
npm run api:generate   # backend/openapi.json + shared/api/generated 갱신
npm run dev
npm run typecheck
npm run lint
npm run test
npm run build
```

`npm run test`에는 임의 포트의 실제 FastAPI 프로세스를 띄워 UI→generated SDK→PIT mock adapter를
검증하는 E2E가 포함된다. 먼저 `backend`에서 `uv sync`를 실행해 `.venv`를 준비해야 한다.

OpenAPI 생성물은 `src/shared/api/generated`에만 있고 앱 코드는 `shared/api` gateway를 통해서만
접근한다. `npm run api:generate` 뒤 diff가 생기면 backend 계약과 생성물을 같은 변경으로 커밋한다.
