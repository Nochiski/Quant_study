/**
 * 포트가 정말 비어 있는지 확인한다(silent reuse 금지).
 *
 * 잠금이 워크트리 간 실행을 직렬화하지만, 옆 체크아웃에서 개발 서버를 띄워 둔 경우까지 막지는
 * 못한다. 그때 Playwright 는 남의 서버가 health 에 응답하는 것만 보고 진행해 버리므로, 시작 전에
 * 직접 바인드해 보고 막혀 있으면 **즉시 실패**한다. 무엇을 어떻게 비키면 되는지 메시지에 담는다.
 */

import { createServer } from "node:net";

/**
 * @param {number} port
 * @param {string} [host]
 * @returns {Promise<boolean>}
 */
export const isPortFree = (port, host = "127.0.0.1") =>
  new Promise((resolve) => {
    const probe = createServer();
    probe.once("error", () => resolve(false));
    probe.once("listening", () => probe.close(() => resolve(true)));
    probe.listen({ port, host, exclusive: true });
  });

/**
 * @param {ReadonlyArray<{port: number, label: string, env: string}>} required
 * @returns {Promise<void>}
 */
export const assertPortsFree = async (required) => {
  const taken = [];
  for (const entry of required) {
    if (!(await isPortFree(entry.port))) taken.push(entry);
  }
  if (taken.length === 0) return;
  const detail = taken
    .map((entry) => `${entry.label}=${entry.port} (override with ${entry.env})`)
    .join(", ");
  throw new Error(
    "E2E ports are already in use — refusing to run against a server this process did not " +
      `start: ${detail}. Stop the other server, or give this worktree its own ports.`,
  );
};
