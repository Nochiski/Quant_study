import { defineConfig } from "@playwright/test";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendDirectory = dirname(fileURLToPath(import.meta.url));
const backendDirectory = resolve(frontendDirectory, "../backend");
const runtimeDirectory = process.env.STRATEGY_WORKBENCH_E2E_RUNTIME_DIR;
if (runtimeDirectory === undefined || !isAbsolute(runtimeDirectory)) {
  throw new Error(
    "Run Playwright through `npm run test:e2e` so its isolated runtime can be cleaned up.",
  );
}
const runtimeDatabase = join(runtimeDirectory, "strategy-workbench.sqlite3");
const ci = process.env.CI !== undefined;

const browserProject = (
  width: 1440 | 1920,
  height: 900 | 1080,
  colorScheme: "light" | "dark",
) => ({
  name: `chromium-${width}-${colorScheme}`,
  testMatch: /workbench\.infrastructure\.spec\.ts/u,
  use: {
    browserName: "chromium" as const,
    viewport: { width, height },
    colorScheme,
  },
});

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
  projects: [
    browserProject(1440, 900, "light"),
    browserProject(1440, 900, "dark"),
    browserProject(1920, 1080, "light"),
    browserProject(1920, 1080, "dark"),
    {
      name: "chromium-workflow",
      testMatch: /workbench\.workflow\.spec\.ts/u,
      use: {
        browserName: "chromium" as const,
        viewport: { width: 1440, height: 900 },
        colorScheme: "light" as const,
      },
    },
  ],
});
