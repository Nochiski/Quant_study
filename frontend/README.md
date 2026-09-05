# Strategy Workbench Frontend

> 전략 authoring은 verbose YAML/JSON source editor 하나를 사용한다
> ([ADR](../docs/superpowers/specs/2026-09-04-strategy-authoring-contract-adr.md),
> [PLAN.md](../docs/planning/strategy-workbench-yaml-ui/PLAN.md)). Form/Graph/Diff는 backend가
> compile한 같은 StrategySpec의 read-only projection이다.

## M5 Backtest run · professional result

YAML workbench는 현재 `StrategySpec`을 generated SDK로 single run에 제출한다. 사용자는 기본
Persistent Rust core와 패리티/debug용 Python reference, 초기 자본, benchmark, OOS 시작 구간을
설정할 수 있다. 실행 상태는 backend run lifecycle에서 polling하고 완료 결과는 query cache가 소유한다.

결과 화면은 backend `MetricRegistry` 응답을 그대로 사용해 equity/benchmark, drawdown,
monthly return, rolling Sharpe, gross/net exposure와 closed trade를 표시한다. raw metric table은
Full·IS·Validation·OOS·Window scope를 보존하고 `None`은 unavailable reason이 있는 N/A로,
실제 0은 숫자로 구분한다. manifest drawer에서 engine/data/tape hash와 데이터 경고를 확인할 수 있다.

## M4 Portfolio · Risk · Execution

YAML document의 `portfolio`·`risk`·`execution` 계약은 long/short와 N/percentile 선택,
4가지 weighting, eligibility·threshold·regime·liquidity·turnover, gross/net/name/sector 제약과
거래 비용을 편집한다. `debug-strategy`는 generated SDK로 backend의 실제 trace와 TargetTape를 읽어
세션별 score/rank/target/exclusion 이유와 engine capability를 표시한다. 화면은 target 비중을
재계산하지 않으며 T 종가→T+1 시가 실행 계약을 그대로 보여준다.

## M3 Factor editor

`entities/factor`는 generated OpenAPI 타입과 query를 통해 backend Factor Registry만 읽는다.
YAML editor는 catalog·snippet·schema completion으로 팩터 탐색·추가, 가중치와
Lag/Rank/Z-score/Winsorize/Neutralize 노드를 작성한다. Graph projection은 동일한 `StrategySpec`
graph를 typed input port, output type/unit, minimum history, inline validation과 함께 표시한다.
별도 수식이나 DTO는 없다.

저장되는 전략의 의미는 백엔드의 버전된 `StrategySpec`이 소유하며, 이 폴더는 편집 경험과
시각화만 소유한다. YAML-first 전환 후 Form/Graph는 read-only projection이 된다.

## YAML IDE 키보드 작업 흐름 (P6-03)

- `Ctrl/⌘+K`: 명령 팔레트에서 Validate·Save·Backtest, YAML/JSON/Form/Graph/Diff, 세 패널,
  현재 문서의 JSON Pointer·팩터/노드 ID와 테마 선호를 검색한다.
- `Ctrl/⌘+Enter`, `Ctrl/⌘+S`, `Ctrl/⌘+Shift+Enter`: 각각 현재 원문의 검증, 리비전 저장,
  백테스트다. 툴바와 같은 invalid/stale/dirty gate를 사용하며 IME 조합 중에는 실행하지 않는다.
- `Alt+1`~`Alt+5`: 현재 route에서 제공하는 표현 탭만 연다.

패널 크기와 `system`/`light`/`dark` 테마 선호만 versioned local storage에 저장한다. 선택 path와
view는 URL이 소유하고, StrategySpec·서버 revision에는 UI 선호를 넣지 않는다. 실제 dark 색상
token은 P6-04에서 저장된 선호에 연결한다.

## 테마 토큰과 UI primitive (P2-01, P6-04)

- 기본값은 밝은 중성 테마이며 system/light/dark 선호를 지원한다. 색·글꼴·간격은
  `src/app/styles/tokens.css`의 semantic
  token(`--surface-*`, `--border*`, `--text*`, `--accent*`, `--status-*`, `--focus-ring`)만 쓴다. 본문 14px,
  보조 정보는 12px 아래로 내려가지 않는다.
- `src/app/styles/base.css`는 reset·타이포·focus-visible을 소유한다. 색상은 semantic token만
  사용하고 차트 색은 `--chart-series-*`만 쓴다.
- `src/shared/ui`: `Button`, `Tabs`, `Badge`, `Tooltip`, `EmptyState`, `SplitHandle`, `CommandPalette`.
  상태는 색과 함께 글리프/문구로 표시하고, Tabs·SplitHandle·CommandPalette는 키보드로 조작한다.
  문구는 `shared/config/messages.ts`에 ko/en을 함께 추가한다.

## 환경 변수

- `VITE_API_BASE_URL`: backend 주소 (기본 `http://localhost:8000`).
- `VITE_ENABLE_OPERATIONS`: 정확히 `true`일 때만 `/operations/*` placeholder route를 노출한다. 그 외 값은 not-found.

## FSD 의존성 방향

```text
app -> pages -> widgets -> features -> entities -> shared
```

- 같은 레이어의 서로 다른 slice는 직접 import하지 않는다.
- 다른 slice가 쓰는 심볼은 해당 slice의 `index.ts` public API로만 가져온다.
- `entities`는 명사(`strategy`, `factor`, `experiment`, `metric`, `dataset`)다.
- `features`는 사용자 행동(`edit-strategy`, `configure-search`, `debug-strategy`,
  `compare-candidates`, `inspect-run`)이다.
- 여러 entity/feature의 조합은 `widgets`나 `pages`가 한다.
- 서버 데이터는 query cache가 소유하고, 저장된 응답을 client store에 복제하지 않는다.
- frontend에서 지표·팩터·전략 의미를 다시 계산하지 않는다. 백엔드 응답을 표현한다.

Contract Inspector와 debugger는 backend catalog·compile·trace 응답에서 필드 단위·공개 시점·권장
lag·coverage·근거를 읽고, 실제 0·원천 생략 0·결측·미수집·coverage gap을 별도 상태로 표시한다.
그래프 좌표·패널 열림 상태 같은 UI metadata는 spec과 분리한다.

## 개발

Node 22.18 이상을 기준으로 한다. backend가 실행 중일 때 YAML workbench는
`http://localhost:8000`의 document compile/revision, trace, backtest API를 호출한다.

```powershell
cd frontend
npm ci
npm run api:generate   # backend/openapi.json + shared/api/generated 갱신
npm run dev
npm run typecheck
npm run lint
npm run test
npm run build
npm run test:e2e
```

`npm run test`에는 임의 포트의 실제 FastAPI 프로세스를 띄워 UI→generated SDK→PIT mock adapter를
검증하는 통합 테스트가 포함된다. `npm run test:e2e`는 실제 FastAPI와 production preview를 격리된
SQLite에서 함께 띄운다. 먼저 `backend`에서 `uv sync`와
`uv run maturin develop --manifest-path rust/backtest_core/Cargo.toml --release`를 실행해야 기본 Rust
백테스트까지 검증된다.

OpenAPI 생성물은 `src/shared/api/generated`에만 있고 앱 코드는 `shared/api` gateway를 통해서만
접근한다. `npm run api:generate` 뒤 diff가 생기면 backend 계약과 생성물을 같은 변경으로 커밋한다.
