# Browser release gate

Playwright owns both ports and starts the real FastAPI server with an isolated temporary SQLite
repository plus the production Vite preview.

## One run per machine

`npm run test:e2e` takes a machine-wide lock (`quant-e2e.lock` in the OS temp directory) before it
starts anything, and waits if another checkout holds it. Do not bypass it by calling `playwright
test` directly.

The lock exists because `reuseExistingServer: false` only checks the port at startup. If a second
worktree grabs port 8000 in the window between that check and our uvicorn binding, our backend dies
and Playwright proceeds against the other checkout's server, which answers the health probe. The
browser then tests somebody else's code. That failed loudly for us once, with stale English
diagnostics on screen, but the dangerous case is the one that passes.

The lock is a directory holding a `pid` file with a single decimal pid. That shape is a contract,
not an implementation detail: other tooling on this machine takes the same lock, and a lock only
works when every participant recognises the others. Do not change the path or the shape without
changing them too.

If an outer process already holds the same lock and runs the gate inside it, set
`QUANT_E2E_LOCK_HELD=1` so the runner does not wait for a lock its own caller is holding. It then
neither takes nor releases it, because releasing would take the lock away from the caller.

A lock left behind by a killed run is reclaimed automatically: the holder's pid is checked for
liveness first. Waiting polls every 15 seconds, prints one line per minute, and gives up after 40
minutes. `e2e/lock.test.mjs` pins those rules.

Two further guards back it up. The runner refuses to start when either port is already bound, so a
stray dev server produces a clear error instead of a silent reuse. And `PW_BACKEND_PORT` /
`PW_PREVIEW_PORT` move both ports, so a worktree can have its own pair:

```text
PW_BACKEND_PORT=18000 PW_PREVIEW_PORT=15173 npm run test:e2e
```

Both values come from `e2e/ports.mjs`. The runner passes the matching API base URL to the build
child process only; `vite.config.ts` deliberately never reads these variables, because vitest and
`npm run dev` load the same config and would follow the port into a backend that is not running.
Never spell a port literally anywhere else.

## What the lock does not cover

- It is per user, not per machine. `os.tmpdir()` is a user directory on Windows, so two accounts on
  the same box do not see each other's lock.
- A recycled pid looks alive. If the holder dies and the operating system hands its pid to another
  process, the lock is held until the 40-minute cap. That fails loudly rather than silently, which
  is the direction we want, but it costs a run.
- It serialises this gate only. Anything else that binds the same ports, a stray dev server for
  instance, is caught by the port check rather than the lock.

Moving the preview port also moves the browser origin, so the backend has to accept it. Playwright
passes the preview origin to the server as `STRATEGY_WORKBENCH_ALLOWED_ORIGINS`. Without that the
server starts healthy and every request from the page is blocked by CORS, which looks like an empty
screen rather than an error.

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

`test:e2e:update` passes `--update-snapshots=changed` on purpose. A bare `--update-snapshots`
leaves a mismatching baseline untouched and still reports the test as passed, so the stale PNG
survives and the next strict run fails again. `=all` rewrites every baseline including the ones
that already matched, which buries the intended change in unrelated byte churn. Keep the mode
explicit, and check `git status` afterwards: only the baselines you meant to change should appear.

This layer owns browser process, server lifecycle, viewport/theme matrix, screenshots and failure
artifacts. The four visual projects collect only `workbench.infrastructure.spec.ts`; one 1440px
light project collects `workbench.workflow.spec.ts` so stateful create/revision/backtest scenarios
execute once against the isolated real backend, and a second one collects
`assistant.workflow.spec.ts`. Backend contract meaning continues to be owned by the backend and its
generated client.

## AI assistant scenarios

`assistant.workflow.spec.ts` drives provider setup, the sidebar chat, proposal apply and the
follow-on backtest. The provider is a **scripted backend adapter**
(`adapters/outbound/llm_scripted`), turned on for this run by
`STRATEGY_WORKBENCH_ASSISTANT_FAKE_PROVIDER=1` in `playwright.config.ts`. No SDK, key or network is
involved, and the flag is off everywhere else.

Faking the model rather than the HTTP responses is deliberate: intercepting the provider in the
browser would take SSE framing, sequence numbers, the turn runner and server-side proposal
re-validation out of the gate. Only the model is fake here.

The script picks a scenario from a keyword in the question (`_scenarios.py`): 제안 asks for a tool
call followed by a proposal, 검색 shows search activity and then three rejected proposals, 천천히
streams a long answer so a mid-turn reload exercises resume, and anything else gets a short answer.

Assistant history and secrets go to the same isolated runtime directory as the strategy database
(`e2e/runtime.ts`); without that the run would write into the developer's real chat history and
`secrets.json`.

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
