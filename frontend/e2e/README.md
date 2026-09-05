# Browser release gate

Playwright owns both ports and starts the real FastAPI server with an isolated temporary SQLite
repository plus the production Vite preview. Stop local servers on ports 5173 and 8000 first.

From `frontend`:

```text
npx playwright install chromium
npm run test:e2e
```

The four Windows Chromium baselines (1440×900 and 1920×1080, light and dark) are authoritative
because the CI browser job also runs on `windows-latest`. Regenerate them on Windows only after an
intentional visual change:

```text
npm run test:e2e:update
npm run test:e2e:report
```

This layer owns browser process, server lifecycle, viewport/theme matrix, screenshots and failure
artifacts. Workflow scenarios and product assertions belong to P6-06; backend contract meaning
continues to be owned by the backend and its generated client.
