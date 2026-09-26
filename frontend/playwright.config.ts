import { defineConfig } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  backendOrigin,
  backendPort,
  previewOrigin,
  previewPort,
} from "./e2e/ports.mjs";
import {
  runtimeAssistantDatabasePath,
  runtimeAssistantSecretsPath,
  runtimeDatabasePath,
} from "./e2e/runtime";

const frontendDirectory = dirname(fileURLToPath(import.meta.url));
const backendDirectory = resolve(frontendDirectory, "../backend");
// 격리 런타임 밖에서 실행되면 여기서 거부한다(run-playwright.mjs만 이 변수를 설정한다).
const runtimeDatabase = runtimeDatabasePath();
// 어시스턴트 이력과 비밀도 같은 격리 런타임 안에 둔다. 기본 경로는 저장소 `.local/`과 OS 사용자
// 설정 디렉터리라, 넘기지 않으면 e2e가 개발자의 실제 대화 이력과 `secrets.json`에 쓴다.
const runtimeAssistantDatabase = runtimeAssistantDatabasePath();
const runtimeAssistantSecrets = runtimeAssistantSecretsPath();
const ci = process.env.CI !== undefined;
// 실데이터 opt-in: `E2E_REAL_EQUITY_ROOT`(로컬 equity 루트, `ledger_sync sync` 산출)가 있으면 backend 를
// duckdb 어댑터로 띄우고 `real-equity` project 만 수집한다. 없으면 mock 어댑터 + 릴리스 게이트 project 만.
// 한 실행의 backend 는 어댑터 하나뿐이라 두 집합은 절대 섞이지 않는다. 셸에 남은
// STRATEGY_WORKBENCH_EQUITY_* 는 여기서 덮어써 릴리스 게이트가 실데이터로 돌지 않게 한다.
const realEquityRoot = process.env.E2E_REAL_EQUITY_ROOT ?? "";
const realEquity = realEquityRoot !== "";
// 워크트리마다 다른 포트를 줄 수 있다(`e2e/ports.mjs`). 설정·webServer·spec 이 같은 값을 읽는다.
const backend = backendOrigin();
const preview = previewOrigin();

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
  {
    // AI 어시스턴트 시나리오. 가짜 공급자 위에서 돌고 스크린샷을 만들지 않으므로 시각 기준선
    // 매트릭스와 분리해 1440 light 하나로 수집한다.
    name: "chromium-assistant",
    testMatch: /assistant\.workflow\.spec\.ts/u,
    use: chromiumUse(1440, 900, "light"),
  },
  {
    // 유저 스토리 하네스(`docs/product/user-stories/`)가 스토리 하나를 위해 새로 쓴 e2e. 스크린샷
    // 기준선을 만들지 않으므로 1440 light 하나로 수집한다. 기존 spec에 붙인 스토리 태그는 그 spec의
    // project에서 돈다.
    name: "chromium-stories",
    testMatch: /stories[\\/][^\\/]+\.spec\.ts/u,
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
    baseURL: preview,
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
        "uv run uvicorn strategy_workbench.bootstrap.facade.http:build_runtime_http_app " +
        `--factory --host 127.0.0.1 --port ${backendPort()}`,
      cwd: backendDirectory,
      env: {
        ...process.env,
        STRATEGY_WORKBENCH_DB_PATH: runtimeDatabase,
        // 포트를 옮기면 preview origin 도 바뀐다. 허용 목록에 넣지 않으면 브라우저 요청이
        // CORS 로 막혀 서버는 멀쩡한데 화면만 빈다.
        STRATEGY_WORKBENCH_ALLOWED_ORIGINS: preview,
        STRATEGY_WORKBENCH_EQUITY_ADAPTER: realEquity ? "duckdb" : "mock",
        STRATEGY_WORKBENCH_EQUITY_ROOT: realEquityRoot,
        STRATEGY_WORKBENCH_ASSISTANT_DB_PATH: runtimeAssistantDatabase,
        STRATEGY_WORKBENCH_ASSISTANT_SECRETS_PATH: runtimeAssistantSecrets,
        // 대본 공급자. 실 SDK·키·네트워크 없이 어시스턴트 시나리오가 돈다(WORKFLOW B-05).
        STRATEGY_WORKBENCH_ASSISTANT_FAKE_PROVIDER: "1",
      },
      url: `${backend}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `npm run preview -- --host 127.0.0.1 --port ${previewPort()} --strictPort`,
      cwd: frontendDirectory,
      url: preview,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
  projects: realEquity ? realEquityProjects : mockProjects,
});
