import { isAbsolute, join } from "node:path";

/** `npm run test:e2e`(run-playwright.mjs)가 만든 격리 런타임 디렉터리를 가리키는 환경 변수. */
export const RUNTIME_DIRECTORY_ENV = "STRATEGY_WORKBENCH_E2E_RUNTIME_DIR";

/**
 * backend 프로세스에만 전달되는 SQLite 경로. Playwright 설정과 spec(1.0 동결 row seeding)이 같은
 * 파일을 가리키도록 한 곳에서 계산한다.
 */
export const runtimeDatabasePath = (): string => {
  const runtimeDirectory = process.env[RUNTIME_DIRECTORY_ENV];
  if (runtimeDirectory === undefined || !isAbsolute(runtimeDirectory)) {
    throw new Error(
      "Run Playwright through `npm run test:e2e` so its isolated runtime can be cleaned up.",
    );
  }
  return join(runtimeDirectory, "strategy-workbench.sqlite3");
};
