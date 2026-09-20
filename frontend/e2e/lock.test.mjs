/**
 * E2E 잠금·포트 유틸 단위 테스트.
 *
 * 이 잠금이 지키는 것은 "한 머신에서 e2e 가 한 번에 하나만 돈다"는 불변식이다. 깨지면 브라우저가
 * 옆 워크트리의 backend 를 테스트하고도 초록으로 통과한다 — 그래서 회수 규칙까지 테스트로 고정한다.
 */

import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import {
  acquireLock,
  isProcessAlive,
  readLockOwner,
  releaseLock,
  tryAcquireLock,
} from "./lock.mjs";
import {
  BACKEND_PORT_ENV,
  DEFAULT_BACKEND_PORT,
  DEFAULT_PREVIEW_PORT,
  backendOrigin,
  backendPort,
  previewPort,
  readPort,
} from "./ports.mjs";
import { isPortFree } from "./free-port.mjs";

const directories = [];
const lockPath = () => {
  const directory = mkdtempSync(join(tmpdir(), "quant-e2e-lock-test-"));
  directories.push(directory);
  return join(directory, "quant-e2e.lock");
};

afterEach(() => {
  for (const directory of directories.splice(0))
    rmSync(directory, { recursive: true, force: true });
});

const owner = (pid) => ({
  pid,
  workdir: `/w/${pid}`,
  startedAt: "2026-09-21T00:00:00.000Z",
});
/** 절대 존재할 수 없는 pid — 회수 경로를 태우려고 쓴다. */
const DEAD_PID = 2 ** 31 - 1;

describe("E2E 잠금", () => {
  it("한 번에 하나만 잡는다", () => {
    const path = lockPath();

    expect(tryAcquireLock(path, owner(process.pid))).toBe(true);
    expect(tryAcquireLock(path, owner(process.pid))).toBe(false);
    expect(readLockOwner(path)?.pid).toBe(process.pid);
  });

  it("놓으면 다음 사람이 잡는다", () => {
    const path = lockPath();
    tryAcquireLock(path, owner(process.pid));

    expect(releaseLock(path, process.pid)).toBe(true);
    expect(readLockOwner(path)).toBeNull();
    expect(tryAcquireLock(path, owner(process.pid))).toBe(true);
  });

  it("주인이 아니면 남의 잠금을 지우지 않는다", () => {
    const path = lockPath();
    tryAcquireLock(path, owner(process.pid));

    expect(releaseLock(path, process.pid + 1)).toBe(false);
    expect(readLockOwner(path)?.pid).toBe(process.pid);
  });

  it("죽은 주인의 잠금은 회수한다", () => {
    // 비정상 종료로 남은 잠금이 머신을 영원히 막으면 안 된다.
    const path = lockPath();
    writeFileSync(path, JSON.stringify(owner(DEAD_PID)), "utf8");
    expect(isProcessAlive(DEAD_PID)).toBe(false);

    expect(tryAcquireLock(path, owner(process.pid))).toBe(true);
    expect(readLockOwner(path)?.pid).toBe(process.pid);
  });

  it("깨진 잠금 파일도 주인 없는 것으로 보고 회수한다", () => {
    const path = lockPath();
    writeFileSync(path, "not json", "utf8");

    expect(readLockOwner(path)).toBeNull();
    expect(tryAcquireLock(path, owner(process.pid))).toBe(true);
  });

  it("살아 있는 주인은 기다렸다가 놓으면 잡는다", async () => {
    const path = lockPath();
    tryAcquireLock(path, owner(process.pid));
    let waited = 0;
    const acquired = acquireLock({
      path,
      pid: process.pid + 1,
      pollMs: 1,
      timeoutMs: 10_000,
      sleep: async () => {
        waited += 1;
        if (waited === 3) releaseLock(path, process.pid);
      },
    });

    const handle = await acquired;

    expect(waited).toBe(3);
    expect(readLockOwner(path)?.pid).toBe(process.pid + 1);
    handle.release();
  });

  it("기다리다 시간을 넘기면 주인을 담아 실패한다", async () => {
    const path = lockPath();
    tryAcquireLock(path, owner(process.pid));
    let clock = 0;

    await expect(
      acquireLock({
        path,
        pid: process.pid + 1,
        pollMs: 1,
        timeoutMs: 5,
        now: () => (clock += 10),
        sleep: async () => {},
      }),
    ).rejects.toThrow(`holder_pid=${process.pid}`);
  });
});

describe("E2E 포트", () => {
  it("기본값은 8000·5173이다", () => {
    expect(backendPort({})).toBe(DEFAULT_BACKEND_PORT);
    expect(previewPort({})).toBe(DEFAULT_PREVIEW_PORT);
    expect(backendOrigin({})).toBe(`http://localhost:${DEFAULT_BACKEND_PORT}`);
  });

  it("환경 변수로 바꾼다", () => {
    expect(backendPort({ [BACKEND_PORT_ENV]: "18000" })).toBe(18000);
    expect(backendOrigin({ [BACKEND_PORT_ENV]: "18000" })).toBe(
      "http://localhost:18000",
    );
  });

  it("포트가 아닌 값은 조용히 기본값으로 돌아가지 않고 실패한다", () => {
    // 오타(`PW_BACKEND_PORT=800O`)가 조용히 8000으로 돌아가면 두 워크트리가 같은 포트를 쓴다.
    for (const bad of ["800O", "0", "80", "70000", "8000.5"]) {
      expect(() =>
        readPort("PW_BACKEND_PORT", 8000, { PW_BACKEND_PORT: bad }),
      ).toThrow("PW_BACKEND_PORT");
    }
    expect(readPort("PW_BACKEND_PORT", 8000, { PW_BACKEND_PORT: "  " })).toBe(
      8000,
    );
  });

  it("쓰이고 있는 포트를 비어 있다고 하지 않는다", async () => {
    const { createServer } = await import("node:net");
    const server = createServer();
    await new Promise((done) =>
      server.listen({ port: 0, host: "127.0.0.1" }, () => done(undefined)),
    );
    const address = server.address();
    const port =
      typeof address === "object" && address !== null ? address.port : 0;

    expect(await isPortFree(port)).toBe(false);
    await new Promise((done) => server.close(() => done(undefined)));
    expect(await isPortFree(port)).toBe(true);
  });
});
