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
equity adapter instead of the mock. It is collected by the `real-equity` project and skips itself
unless both variables are set, so CI (mock) always reports it as skipped:

```text
$env:STRATEGY_WORKBENCH_EQUITY_ADAPTER = "duckdb"
$env:STRATEGY_WORKBENCH_EQUITY_ROOT = "$HOME\quant-ledger\data\equity"
npm run test:e2e -- --project real-equity
```

The local equity root comes from `database/scripts/ledger_sync.ps1 sync` (see
`database/docs/LEDGER_SYNC.md`). Expect the backtest start request to take about 80 seconds on the
full common-stock universe: the backend builds the TargetTape synchronously before it returns the
run id.
