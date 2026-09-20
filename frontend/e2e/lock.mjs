/**
 * 머신 단위 E2E 잠금.
 *
 * 왜 필요한가. Playwright `webServer` 의 `reuseExistingServer: false` 는 **시작 시점** 포트만
 * 본다. 한 머신의 다른 워크트리가 그 사이 8000 을 잡으면 우리 uvicorn 은 바인드에 실패해 죽고,
 * Playwright 는 남의 backend 가 health 에 응답하는 것만 보고 그대로 진행한다. 그러면 브라우저가
 * 옆 체크아웃의 코드를 테스트한다 — 우리는 옛 영문 진단이 화면에 찍혀 알아챘지만, 조용히 통과해
 * 버리는 쪽이 더 위험하다. 그래서 실행 자체를 머신 단위로 직렬화한다.
 *
 * 잠금은 `%TEMP%/quant-e2e.lock` 파일이고 주인은 pid 다. 주인 프로세스가 죽었으면(비정상 종료,
 * 강제 kill) 잠금이 영원히 남지 않도록 pid 생존을 확인해 회수한다.
 */

import {
  closeSync,
  openSync,
  readFileSync,
  rmSync,
  statSync,
  writeSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const LOCK_FILE_NAME = "quant-e2e.lock";
export const DEFAULT_LOCK_PATH = join(tmpdir(), LOCK_FILE_NAME);
/** 기본 최대 대기: 한 번의 게이트가 2분 안팎이라 줄 서 있는 워크트리 몇 개는 기다려 준다. */
export const DEFAULT_TIMEOUT_MS = 30 * 60 * 1000;
export const DEFAULT_POLL_MS = 2000;

/**
 * pid 가 살아 있는가. `kill(pid, 0)` 은 신호를 보내지 않고 존재만 확인한다.
 * `EPERM` 은 "있는데 내 권한 밖"이라 살아 있는 것으로 센다 — 남의 잠금을 뺏지 않는다.
 * @param {number} pid
 */
export const isProcessAlive = (pid) => {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return /** @type {NodeJS.ErrnoException} */ (error).code === "EPERM";
  }
};

/** 잠금 자리가 디렉터리면 그 안의 `pid` 파일이 주인이다(mkdir 방식 잠금). */
const DIRECTORY_PID_FILE = "pid";

/**
 * 잠금 자리의 주인. 비어 있거나 읽을 수 없으면 null(깨진 잠금은 주인 없는 것으로 본다).
 *
 * 같은 자리를 mkdir 방식(디렉터리 + 그 안의 `pid` 파일)으로 잡는 구현과 한 머신에서 만날 수
 * 있다. 모양이 다르다고 터지면 서로를 못 본 채 둘 다 돌아 버리므로 — 잠금이 있으나 마나가 된다 —
 * 두 모양을 다 읽는다. 누가 먼저 만들었든 나머지는 기다린다.
 * @param {string} path
 */
export const readLockOwner = (path) => {
  let raw;
  try {
    raw = statSync(path).isDirectory()
      ? readFileSync(join(path, DIRECTORY_PID_FILE), "utf8")
      : readFileSync(path, "utf8");
  } catch (error) {
    if (/** @type {NodeJS.ErrnoException} */ (error).code === "ENOENT")
      return null;
    throw error;
  }
  try {
    const owner = JSON.parse(raw);
    if (typeof owner?.pid === "number") return owner;
  } catch {
    // JSON 이 아니면 mkdir 방식이 적은 pid 숫자 하나다.
  }
  const pid = Number(raw.trim());
  if (!Number.isInteger(pid) || pid <= 0) return null;
  return { pid, workdir: "(directory lock)", startedAt: "" };
};

/**
 * 한 번만 시도한다. 잡았으면 true, 살아 있는 주인이 있으면 false.
 * 죽은 주인의 잠금은 한 번 회수하고 다시 시도한다(그 사이 다른 프로세스가 잡으면 false).
 * @param {string} path
 * @param {{pid: number, workdir: string, startedAt: string}} owner
 * @param {boolean} [reclaimed]
 */
export const tryAcquireLock = (path, owner, reclaimed = false) => {
  let fd;
  try {
    // `wx` 는 "없을 때만 생성"이라 같은 순간에 둘이 잡는 일이 없다.
    fd = openSync(path, "wx");
  } catch (error) {
    if (/** @type {NodeJS.ErrnoException} */ (error).code !== "EEXIST")
      throw error;
    if (reclaimed) return false;
    const existing = readLockOwner(path);
    if (existing !== null && isProcessAlive(existing.pid)) return false;
    // 디렉터리 방식 잠금도 회수 대상이라 recursive 로 지운다.
    rmSync(path, { force: true, recursive: true });
    return tryAcquireLock(path, owner, true);
  }
  try {
    writeSync(fd, JSON.stringify(owner));
  } finally {
    closeSync(fd);
  }
  return true;
};

/**
 * 내가 주인일 때만 지운다. 이미 회수돼 남이 잡고 있으면 건드리지 않는다.
 * @param {string} path
 * @param {number} pid
 */
export const releaseLock = (path, pid) => {
  const owner = readLockOwner(path);
  if (owner === null || owner.pid !== pid) return false;
  rmSync(path, { force: true });
  return true;
};

/**
 * 잡을 때까지 기다린다. 시간을 넘기면 마지막 주인을 담은 오류를 던진다.
 * @param {object} options
 * @param {string} [options.path]
 * @param {number} [options.pid]
 * @param {string} [options.workdir]
 * @param {number} [options.timeoutMs]
 * @param {number} [options.pollMs]
 * @param {(owner: {pid: number, workdir: string, startedAt: string} | null) => void} [options.onWait]
 * @param {() => number} [options.now]
 * @param {(ms: number) => Promise<void>} [options.sleep]
 */
export const acquireLock = async ({
  path = DEFAULT_LOCK_PATH,
  pid = process.pid,
  workdir = process.cwd(),
  timeoutMs = DEFAULT_TIMEOUT_MS,
  pollMs = DEFAULT_POLL_MS,
  onWait,
  now = Date.now,
  sleep = (ms) => new Promise((done) => setTimeout(done, ms)),
} = {}) => {
  const owner = { pid, workdir, startedAt: new Date(now()).toISOString() };
  const deadline = now() + timeoutMs;
  let announced = false;
  for (;;) {
    if (tryAcquireLock(path, owner))
      return { path, pid, release: () => releaseLock(path, pid) };
    const holder = readLockOwner(path);
    if (!announced) {
      onWait?.(holder);
      announced = true;
    }
    if (now() >= deadline) {
      throw new Error(
        "timed out waiting for the machine-wide E2E lock — " +
          `path=${path} waited_ms=${timeoutMs} holder_pid=${holder?.pid ?? "unknown"} ` +
          `holder_workdir=${holder?.workdir ?? "unknown"}`,
      );
    }
    await sleep(pollMs);
  }
};
