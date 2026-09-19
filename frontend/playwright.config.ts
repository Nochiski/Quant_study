import { defineConfig } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { runtimeDatabasePath } from "./e2e/runtime";

const frontendDirectory = dirname(fileURLToPath(import.meta.url));
const backendDirectory = resolve(frontendDirectory, "../backend");
// 격리 런타임 밖에서 실행되면 여기서 거부한다(run-playwright.mjs만 이 변수를 설정한다).
const runtimeDatabase = runtimeDatabasePath();
const ci = process.env.CI !== undefined;
// 실데이터 opt-in: `E2E_REAL_EQUITY_ROOT`(로컬 equity 루트, `ledger_sync sync` 산출)가 있으면 backend 를
// duckdb 어댑터로 띄우고 `real-equity` project 만 수집한다. 없으면 mock 어댑터 + 릴리스 게이트 project 만.
// 한 실행의 backend 는 어댑터 하나뿐이라 두 집합은 절대 섞이지 않는다. 셸에 남은
// STRATEGY_WORKBENCH_EQUITY_* 는 여기서 덮어써 릴리스 게이트가 실데이터로 돌지 않게 한다.
const realEquityRoot = process.env.E2E_REAL_EQUITY_ROOT ?? "";
const realEquity = realEquityRoot !== "";

const chromiumUse = (
  width: 1440 | 1920,
  height: 900 | 1080,
  colorScheme: "light" | "dark",
) => ({
  browserName: "chromium" as const,
  viewport: { width, height },
  colorScheme,
});

const browserProject = (
  width: 1440 | 1920,
  height: 900 | 1080,
  colorScheme: "light" | "dark",
) => ({
  name: `chromium-${width}-${colorScheme}`,
  testMatch: /workbench\.infrastructure\.spec\.ts/u,
  use: chromiumUse(width, height, colorScheme),
});

const mockProjects = [
  browserProject(1440, 900, "light"),
  browserProject(1440, 900, "dark"),
  browserProject(1920, 1080, "light"),
  browserProject(1920, 1080, "dark"),
  {
    name: "chromium-workflow",
    testMatch: /workbench\.workflow\.spec\.ts/u,
    use: chromiumUse(1440, 900, "light"),
  },
];

const realEquityProjects = [
  {
    name: "real-equity",
    testMatch: /workbench\.real-equity\.spec\.ts/u,
    use: chromiumUse(1440, 900, "light"),
  },
];

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  snapshotPathTemplate:
    "{testDir}/__screenshots__/{testFilePath}/{projectName}/{arg}{ext}",
  fullyParallel: false,
  workers: 1,
  retries: ci ? 1 : 0,
  reporter: ci
    ? [["line"], ["html", { open: "never" }]]
    : [["list"], ["html", { open: "never" }]],
  expect: {
    timeout: 10_000,
    toHaveScreenshot: {
      animations: "disabled",
      caret: "hide",
      scale: "css",
      maxDiffPixels: 0,
    },
  },
  use: {
    baseURL: "http://localhost:5173",
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
    // `locale` 은 navigator.language 와 Intl 만 바꾼다. Windows Chromium 의
    // `<input type="date">` 자리표시자(yyyy-mm-dd / mm/dd/yyyy)는 브라우저 UI 언어를
    // 따르므로, 호스트 OS 로케일과 무관하게 기준선이 재현되도록 UI 언어도 고정한다.
    launchOptions: { args: ["--lang=ko-KR"] },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      // `uv run server` 는 `--reload` 를 켠다. uvicorn 0.52 의 Windows 재시작은
      // CTRL_C_EVENT 를 콘솔 전체에 보내 Playwright 까지 함께 종료시키고, CI 러너에서는
      // `.venv` 안 파일 변경 감지가 곧바로 재시작을 일으켜 브라우저 게이트가 한 번도
      // 통과하지 못했다. E2E 는 코드가 바뀌지 않으므로 같은 앱을 reload 없이 띄운다.
      command:
        "uv run uvicorn strategy_workbench.bootstrap.facade.http:build_runtime_http_app --factory --host 127.0.0.1 --port 8000",
      cwd: backendDirectory,
      env: {
        ...process.env,
        STRATEGY_WORKBENCH_DB_PATH: runtimeDatabase,
        STRATEGY_WORKBENCH_EQUITY_ADAPTER: realEquity ? "duckdb" : "mock",
        STRATEGY_WORKBENCH_EQUITY_ROOT: realEquityRoot,
      },
      url: "http://localhost:8000/api/v1/health",
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: "npm run preview -- --host 127.0.0.1 --port 5173 --strictPort",
      cwd: frontendDirectory,
      url: "http://localhost:5173",
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
  projects: realEquity ? realEquityProjects : mockProjects,
});
