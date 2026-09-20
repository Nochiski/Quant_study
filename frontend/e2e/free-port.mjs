/**
 * 포트가 정말 비어 있는지 확인한다(silent reuse 금지).
 *
 * 잠금이 워크트리 간 실행을 직렬화하지만, 옆 체크아웃에서 개발 서버를 띄워 둔 경우까지 막지는
 * 못한다. 그때 Playwright 는 남의 서버가 health 에 응답하는 것만 보고 진행해 버리므로, 시작 전에
 * 직접 바인드해 보고 막혀 있으면 **즉시 실패**한다. 무엇을 어떻게 비키면 되는지 메시지에 담는다.
 */

import { createServer } from "node:net";

/**
 * 한 주소에서 그 포트가 비었는가.
 * @param {number} port
 * @param {string} [host]
 * @returns {Promise<boolean>}
 */
const isPortFreeOn = (port, host) =>
  new Promise((resolve) => {
    const probe = createServer();
    probe.once("error", (error) =>
      // 이 호스트에 주소 자체가 없으면(IPv6 미구성) "쓰이고 있음"이 아니다.
      resolve(
        /** @type {NodeJS.ErrnoException} */ (error).code === "EADDRNOTAVAIL",
      ),
    );
    probe.once("listening", () => probe.close(() => resolve(true)));
    probe.listen({ port, host, exclusive: true });
  });

/**
 * IPv4·IPv6 양쪽에서 비었을 때만 비었다고 한다.
 *
 * `127.0.0.1` 만 보면 `::1` 에만 묶인 서버를 놓친다. Windows 에서 `localhost` 가 `::1` 로 먼저
 * 풀리는 조합이 있어, 그 서버를 못 보고 지나가면 우리가 막으려던 "남의 backend 를 쓴다"가 그대로
 * 난다(1차 리뷰 P3-9).
 * @param {number} port
 * @returns {Promise<boolean>}
 */
export const isPortFree = async (port) => {
  for (const host of ["127.0.0.1", "::1"]) {
    if (!(await isPortFreeOn(port, host))) return false;
  }
  return true;
};

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
