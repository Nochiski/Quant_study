import { isAbsolute, join } from "node:path";

/** `npm run test:e2e`(run-playwright.mjs)가 만든 격리 런타임 디렉터리를 가리키는 환경 변수. */
export const RUNTIME_DIRECTORY_ENV = "STRATEGY_WORKBENCH_E2E_RUNTIME_DIR";

const runtimeDirectory = (): string => {
  const directory = process.env[RUNTIME_DIRECTORY_ENV];
  if (directory === undefined || !isAbsolute(directory)) {
    throw new Error(
      "Run Playwright through `npm run test:e2e` so its isolated runtime can be cleaned up.",
    );
  }
  return directory;
};

/**
 * backend 프로세스에만 전달되는 SQLite 경로. Playwright 설정과 spec(1.0 동결 row seeding)이 같은
 * 파일을 가리키도록 한 곳에서 계산한다.
 */
export const runtimeDatabasePath = (): string =>
  join(runtimeDirectory(), "strategy-workbench.sqlite3");

/**
 * 어시스턴트 대화 이력 DB. 전략 DB와 **다른 파일**인 것은 backend의 구조이고(spec D5), 여기서
 * 중요한 것은 둘 다 격리 런타임 안이라는 점이다. 비우지 않으면 e2e가 개발자의 실제 대화 이력에
 * 세션을 쌓는다.
 */
export const runtimeAssistantDatabasePath = (): string =>
  join(runtimeDirectory(), "assistant.sqlite3");

/**
 * 어시스턴트 비밀 파일. 기본 경로는 OS 사용자 설정 디렉터리라, 넘기지 않으면 e2e가 등록한 가짜
 * 키가 개발자의 실제 `secrets.json`에 섞이고 실행이 끝나도 남는다.
 */
export const runtimeAssistantSecretsPath = (): string =>
  join(runtimeDirectory(), "assistant-secrets.json");
