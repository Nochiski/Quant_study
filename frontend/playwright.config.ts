import { defineConfig } from "@playwright/test";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendDirectory = dirname(fileURLToPath(import.meta.url));
const backendDirectory = resolve(frontendDirectory, "../backend");
const runtimeDatabase = join(
  tmpdir(),
  `quant-strategy-workbench-e2e-${process.pid}.sqlite3`,
);
const ci = process.env.CI !== undefined;

const browserProject = (
  width: 1440 | 1920,
  height: 900 | 1080,
  colorScheme: "light" | "dark",
) => ({
  name: `chromium-${width}-${colorScheme}`,
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
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: "uv run server --host 127.0.0.1 --port 8000",
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
  ],
});
