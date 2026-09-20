# Browser release gate

Playwright owns both ports and starts the real FastAPI server with an isolated temporary SQLite
repository plus the production Vite preview. Stop local servers on ports 5173 and 8000 first.

From `frontend`:

```text
npx playwright install chromium
npm run test:e2e
```

The four Windows Chromium baselines (1440×900 and 1920×1080, light and dark) use Playwright's
exact Chromium build and exact-version bundled Noto Sans KR/JetBrains Mono webfonts. The CI browser
job is pinned to the Windows Server 2025 generation. Regenerate them on Windows only after an
intentional visual change, then require a strict no-update CI pass:

```text
npm run test:e2e:update
npm run test:e2e:report
```

`workbench.workflow.spec.ts`도 기준선 한 장(`graph-node-diagnostic.png`)을 갖는다. 노드 카드
레이아웃 계약은 같은 테스트의 boundingBox 단언이 잠그고 이 이미지는 보조 증거라, 뷰포트·테마
한 벌(1440 light)로 충분해 시각 프로젝트 4종에 넣지 않았다. 캡처는 진단 문장을 `mask`로 가린다 —
그 문장의 owner는 backend라 문구가 다듬어져도 이 기준선을 다시 찍을 일이 없어야 한다.

This layer owns browser process, server lifecycle, viewport/theme matrix, screenshots and failure
artifacts. The four visual projects collect only `workbench.infrastructure.spec.ts`; the single
1440px light project collects `workbench.workflow.spec.ts` so stateful create/revision/backtest
scenarios execute once against the isolated real backend. Backend contract meaning continues to be
owned by the backend and its generated client.

The npm test/update commands atomically create a unique random directory under the operating
system temp root, pass its SQLite path only to the backend process, and delete the whole directory
after Playwright exits. Calling `playwright test` directly is rejected so a reused PID or abandoned
database cannot leak state into a later run.

## Real equity data (opt-in)

`workbench.real-equity.spec.ts` runs the graph-edit → save → backtest scenario against the duckdb
equity adapter instead of the mock. Set `E2E_REAL_EQUITY_ROOT` to the local equity root produced by
`database/scripts/ledger_sync.ps1 sync` (see `database/docs/LEDGER_SYNC.md`):

```text
$env:E2E_REAL_EQUITY_ROOT = "$HOME\quant-ledger\data\equity"
npm run test:e2e
```

With that variable set, `playwright.config.ts` starts the backend with the duckdb adapter and collects
only the `real-equity` project; without it, the backend runs the mock adapter and only the release-gate
projects are collected. The two sets never share a run, so a shell that still has the variable set
cannot turn the mock baselines into real-data failures. CI never sets the variable.

Requirements and expectations:

- The local copy must cover sessions from at least 2023-01 so the 252-session momentum window has
  history for the 2024 backtest window; otherwise the failure shows up as a backtest error, not as a
  data-coverage message.
- The backend builds the TargetTape synchronously before it returns the run id, so the backtest start
  request dominates the run: about 80 seconds of a 1.2-minute spec on the full common-stock universe
  (measured 2026-09-19, Rust core; the engine itself finishes in about 2 seconds).
- The run-settings benchmark defaults to empty (run without a benchmark; the ID vocabulary is
  adapter-owned, issue #154). The spec sets `005930:1` explicitly so the benchmark path is exercised
  against real data.
