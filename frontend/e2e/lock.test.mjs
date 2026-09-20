/**
 * E2E 잠금·포트 유틸 단위 테스트.
 *
 * 이 잠금이 지키는 것은 "한 머신에서 e2e 가 한 번에 하나만 돈다"는 불변식이다. 깨지면 브라우저가
 * 옆 워크트리의 backend 를 테스트하고도 초록으로 통과한다 — 그래서 회수 규칙까지 테스트로 고정한다.
 *
 * 형식(디렉터리 + 십진수 `pid` 파일)은 한 머신의 다른 구현과 같은 잠금을 공유하려고 맞춘 것이라
 * 그 자체가 계약이다. 모양이 달라지면 서로를 주인으로 못 알아보고 둘 다 돌아 버린다.
 */

import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import {
  DEFAULT_LOCK_PATH,
  LOCK_DIRECTORY_NAME,
  PID_FILE_NAME,
  acquireLock,
  isProcessAlive,
  readLockPid,
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
  return join(directory, LOCK_DIRECTORY_NAME);
};

afterEach(() => {
  for (const directory of directories.splice(0))
    rmSync(directory, { recursive: true, force: true });
});

/** 절대 존재할 수 없는 pid — 회수 경로를 태우려고 쓴다. */
const DEAD_PID = 2 ** 31 - 1;

/** 다른 구현(임시 래퍼)이 잠금을 쥔 상태를 그대로 만든다. */
const heldBy = (path, pid) => {
  mkdirSync(path);
  writeFileSync(join(path, PID_FILE_NAME), String(pid));
};

describe("E2E 잠금", () => {
  it("한 머신 기본 경로는 임시 디렉터리의 quant-e2e.lock이다", () => {
    // 다른 구현과 같은 자리를 써야 서로를 주인으로 알아본다.
    expect(DEFAULT_LOCK_PATH).toBe(join(tmpdir(), "quant-e2e.lock"));
  });

  it("디렉터리와 십진수 pid 파일로 잡는다", () => {
    const path = lockPath();

    expect(tryAcquireLock(path, 4242)).toBe(true);
    expect(readFileSync(join(path, PID_FILE_NAME), "utf8")).toBe("4242");
    expect(readLockPid(path)).toBe(4242);
  });

  it("한 번에 하나만 잡는다", () => {
    const path = lockPath();

    expect(tryAcquireLock(path, process.pid)).toBe(true);
    expect(tryAcquireLock(path, process.pid)).toBe(false);
  });

  it("놓으면 다음 사람이 잡는다", () => {
    const path = lockPath();
    tryAcquireLock(path, process.pid);

    expect(releaseLock(path, process.pid)).toBe(true);
    expect(existsSync(path)).toBe(false);
    expect(tryAcquireLock(path, process.pid)).toBe(true);
  });

  it("주인이 아니면 남의 잠금을 지우지 않는다", () => {
    const path = lockPath();
    tryAcquireLock(path, process.pid);

    expect(releaseLock(path, process.pid + 1)).toBe(false);
    expect(readLockPid(path)).toBe(process.pid);
  });

  it("다른 구현이 쥔 잠금도 주인으로 읽고 기다린다", () => {
    const path = lockPath();
    heldBy(path, process.pid);

    expect(readLockPid(path)).toBe(process.pid);
    expect(tryAcquireLock(path, process.pid + 1)).toBe(false);
  });

  it("죽은 주인의 잠금은 회수한다", () => {
    // 비정상 종료로 남은 잠금이 머신을 영원히 막으면 안 된다.
    const path = lockPath();
    heldBy(path, DEAD_PID);
    expect(isProcessAlive(DEAD_PID)).toBe(false);

    expect(tryAcquireLock(path, process.pid)).toBe(true);
    expect(readLockPid(path)).toBe(process.pid);
  });

  it("pid 파일이 없거나 숫자가 아니면 주인 없는 잠금으로 보고 회수한다", () => {
    const path = lockPath();
    mkdirSync(path);

    expect(readLockPid(path)).toBeNull();
    expect(tryAcquireLock(path, process.pid)).toBe(true);

    const broken = lockPath();
    mkdirSync(broken);
    writeFileSync(join(broken, PID_FILE_NAME), "not a pid");

    expect(readLockPid(broken)).toBeNull();
    expect(tryAcquireLock(broken, process.pid)).toBe(true);
  });

  it("살아 있는 주인은 기다렸다가 놓으면 잡는다", async () => {
    const path = lockPath();
    tryAcquireLock(path, process.pid);
    let waited = 0;

    const handle = await acquireLock({
      path,
      pid: process.pid + 1,
      pollMs: 1,
      timeoutMs: 10_000,
      sleep: async () => {
        waited += 1;
        if (waited === 3) releaseLock(path, process.pid);
      },
    });

    expect(waited).toBe(3);
    expect(readLockPid(path)).toBe(process.pid + 1);
    handle.release();
  });

  it("대기 로그는 간격을 두고 한 줄씩만 낸다", async () => {
    // 폴링 줄마다 한 줄이면 에이전트 모니터가 그때마다 깨어난다.
    const path = lockPath();
    tryAcquireLock(path, process.pid);
    const notices = [];
    let clock = 0;

    await expect(
      acquireLock({
        path,
        pid: process.pid + 1,
        pollMs: 1,
        noticeMs: 60_000,
        timeoutMs: 100_000,
        onWait: (holder) => notices.push(holder),
        now: () => (clock += 10_000),
        sleep: async () => {},
      }),
    ).rejects.toThrow("timed out");

    expect(notices.length).toBeLessThan(4);
    expect(notices[0]).toBe(process.pid);
  });

  it("바깥이 이미 잠금을 쥐고 부르면 다시 잡지 않는다", async () => {
    // 래퍼로 감싸 돌릴 때 또 잡으려 들면 자기 자신을 기다리는 교착이 된다.
    const path = lockPath();
    heldBy(path, process.pid);

    const handle = await acquireLock({
      path,
      pid: process.pid + 1,
      outerHeld: true,
      sleep: async () => {
        throw new Error("바깥이 주인이면 기다리지 않아야 한다");
      },
    });

    // 바깥 주인을 그대로 둔다 — 놓으면 남의 잠금을 뺏는 셈이다.
    expect(handle.release()).toBe(false);
    expect(readLockPid(path)).toBe(process.pid);
  });

  it("기다리다 시간을 넘기면 주인을 담아 실패한다", async () => {
    const path = lockPath();
    tryAcquireLock(path, process.pid);
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
