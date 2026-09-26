/**
 * E2E 가 쓰는 두 포트의 정본.
 *
 * 한 머신에 워크트리가 여러 개 있으면 기본 포트가 서로 부딪힌다. 잠금(`lock.mjs`)이 실행을
 * 직렬화하긴 하지만, 옆 체크아웃에서 개발 서버를 띄워 둔 채 게이트를 돌리는 경우까지 막지는
 * 못한다. 그래서 포트 자체를 환경 변수로 옮길 수 있게 한다.
 *
 * 값을 읽는 곳이 네 군데(playwright 설정, webServer 명령, spec 의 backend 주소, 빌드 때 박히는
 * `VITE_API_BASE_URL`)라 상수를 한 곳에 둔다. 한 곳이라도 다른 값을 쓰면 브라우저가 엉뚱한
 * backend 를 보고, 그 증상은 "내 코드인데 옛 동작이 보인다"로 나타나 원인을 찾기 어렵다.
 */

export const BACKEND_PORT_ENV = "PW_BACKEND_PORT";
export const PREVIEW_PORT_ENV = "PW_PREVIEW_PORT";
export const DEFAULT_BACKEND_PORT = 8000;
export const DEFAULT_PREVIEW_PORT = 5173;

/**
 * @param {string} name
 * @param {number} fallback
 * @param {NodeJS.ProcessEnv} [env]
 * @returns {number}
 */
export const readPort = (name, fallback, env = process.env) => {
  const raw = env[name];
  if (raw === undefined || raw.trim() === "") return fallback;
  const port = Number(raw);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) {
    throw new Error(
      `${name} must be an integer port between 1024 and 65535 — got=${JSON.stringify(raw)}`,
    );
  }
  return port;
};

/** @param {NodeJS.ProcessEnv} [env] */
export const backendPort = (env) =>
  readPort(BACKEND_PORT_ENV, DEFAULT_BACKEND_PORT, env);

/** @param {NodeJS.ProcessEnv} [env] */
export const previewPort = (env) =>
  readPort(PREVIEW_PORT_ENV, DEFAULT_PREVIEW_PORT, env);

/** @param {NodeJS.ProcessEnv} [env] */
export const backendOrigin = (env) => `http://localhost:${backendPort(env)}`;

/** @param {NodeJS.ProcessEnv} [env] */
export const previewOrigin = (env) => `http://localhost:${previewPort(env)}`;
