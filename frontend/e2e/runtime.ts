import { isAbsolute, join } from "node:path";

/**
 * backend 가 실험 trial run 을 엔진 앞에서 붙잡아 두는 초(`STRATEGY_WORKBENCH_E2E_TRIAL_HOLD_SECONDS`).
 * mock 실행은 1초 안에 끝나 실험의 대기·일시정지·취소를 화면에서 볼 수 없다. 사용자 단일 실행은 붙잡지
 * 않으므로 실험을 만들지 않는 spec 에는 영향이 없다. 설정(webServer env)과 spec 의 대기 시간이 이 값을 읽는다.
 */
export const TRIAL_HOLD_SECONDS = 15;

/** `npm run test:e2e`(run-playwright.mjs)가 만든 격리 런타임 디렉터리를 가리키는 환경 변수. */
export const RUNTIME_DIRECTORY_ENV = "STRATEGY_WORKBENCH_E2E_RUNTIME_DIR";
/** 100종목 예제 앱만 쓰는 경로. 기존 3종목 회귀 앱과 DB·데이터를 분리한다. */
export const IDEAS_API_PREFIX = "/ideas";

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
 * backend 프로세스에만 전달되는 SQLite 경로. Playwright 설정과 spec(은퇴 버전 동결 row seeding)이 같은
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

/**
 * 백테스트 실행 기록 DB. 기본 경로는 저장소 `.local/`이라, 넘기지 않으면 e2e 실행이 개발자의 실제 이력에
 * 쌓이고 개발 서버가 도는 run 을 재시작 때 `interrupted`로 닫는다.
 */
export const runtimeResearchDatabasePath = (): string =>
  join(runtimeDirectory(), "research.sqlite3");
