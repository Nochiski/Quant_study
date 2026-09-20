/**
 * 머신 단위 E2E 잠금.
 *
 * 왜 필요한가. Playwright `webServer` 의 `reuseExistingServer: false` 는 **시작 시점** 포트만
 * 본다. 한 머신의 다른 워크트리가 그 사이 8000 을 잡으면 우리 uvicorn 은 바인드에 실패해 죽고,
 * Playwright 는 남의 backend 가 health 에 응답하는 것만 보고 그대로 진행한다. 그러면 브라우저가
 * 옆 체크아웃의 코드를 테스트한다 — 우리는 옛 영문 진단이 화면에 찍혀 알아챘지만, 조용히 통과해
 * 버리는 쪽이 더 위험하다. 그래서 실행 자체를 머신 단위로 직렬화한다.
 *
 * 형식은 **디렉터리**다: `os.tmpdir()/quant-e2e.lock` 를 `mkdir` 로 만들고(원자적) 그 안의
 * `pid` 파일에 십진수 pid 하나만 적는다. 파일+JSON 이 아니라 이 모양인 이유는 한 머신에서 도는
 * 다른 구현들과 **같은 잠금을 공유해야** 하기 때문이다. 모양이 다르면 서로를 주인으로 알아보지
 * 못해 둘 다 그냥 돌아 버리고, 잠금이 있으나 마나가 된다.
 *
 * 주인 프로세스가 죽었으면(비정상 종료, 강제 kill) 잠금이 영원히 남지 않도록 pid 생존을 확인해
 * 회수한다.
 */

import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const LOCK_DIRECTORY_NAME = "quant-e2e.lock";
export const DEFAULT_LOCK_PATH = join(tmpdir(), LOCK_DIRECTORY_NAME);
export const PID_FILE_NAME = "pid";
/** 최대 대기 40분: 앞선 게이트 한 번이 2분 안팎이라 줄 서 있는 워크트리 몇 개는 기다려 준다. */
export const DEFAULT_TIMEOUT_MS = 40 * 60 * 1000;
export const DEFAULT_POLL_MS = 15_000;
/** 대기 로그는 60초에 한 줄만 — 폴링 줄마다 에이전트 모니터가 깨어나지 않게. */
export const DEFAULT_NOTICE_MS = 60_000;

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

/**
 * 잠금을 쥔 pid. 잠금이 없거나 `pid` 파일이 없거나 숫자가 아니면 null(주인 없는 잠금으로 본다).
 * @param {string} path
 * @returns {number | null}
 */
export const readLockPid = (path) => {
  let raw;
  try {
    raw = readFileSync(join(path, PID_FILE_NAME), "utf8");
  } catch (error) {
    const code = /** @type {NodeJS.ErrnoException} */ (error).code;
    // 잠금 자체가 없거나(ENOENT) 디렉터리가 아니거나(ENOTDIR) 막 만들어지는 중이면 주인 없음.
    if (code === "ENOENT" || code === "ENOTDIR") return null;
    throw error;
  }
  const pid = Number(raw.trim());
  return Number.isInteger(pid) && pid > 0 ? pid : null;
};

/**
 * 한 번만 시도한다. 잡았으면 true, 살아 있는 주인이 있으면 false.
 * 죽은 주인의 잠금은 한 번 회수하고 다시 시도한다(그 사이 다른 프로세스가 잡으면 false).
 * @param {string} path
 * @param {number} pid
 * @param {boolean} [reclaimed]
 */
export const tryAcquireLock = (path, pid, reclaimed = false) => {
  try {
    // `mkdir` 은 "없을 때만 생성"이라 같은 순간에 둘이 잡는 일이 없다.
    mkdirSync(path);
  } catch (error) {
    if (/** @type {NodeJS.ErrnoException} */ (error).code !== "EEXIST")
      throw error;
    if (reclaimed) return false;
    const holder = readLockPid(path);
    if (holder !== null && isProcessAlive(holder)) return false;
    rmSync(path, { recursive: true, force: true });
    return tryAcquireLock(path, pid, true);
  }
  writeFileSync(join(path, PID_FILE_NAME), String(pid));
  return true;
};

/**
 * 내가 주인일 때만 지운다. 이미 회수돼 남이 잡고 있으면 건드리지 않는다.
 * @param {string} path
 * @param {number} pid
 */
export const releaseLock = (path, pid) => {
  if (readLockPid(path) !== pid) return false;
  rmSync(path, { recursive: true, force: true });
  return true;
};

/**
 * 잡을 때까지 기다린다. 시간을 넘기면 마지막 주인을 담은 오류를 던진다.
 * @param {object} [options]
 * @param {string} [options.path]
 * @param {number} [options.pid]
 * @param {number} [options.timeoutMs]
 * @param {number} [options.pollMs]
 * @param {number} [options.noticeMs]
 * @param {(pid: number | null) => void} [options.onWait]
 * @param {() => number} [options.now]
 * @param {(ms: number) => Promise<void>} [options.sleep]
 */
export const acquireLock = async ({
  path = DEFAULT_LOCK_PATH,
  pid = process.pid,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  pollMs = DEFAULT_POLL_MS,
  noticeMs = DEFAULT_NOTICE_MS,
  onWait,
  now = Date.now,
  sleep = (ms) => new Promise((done) => setTimeout(done, ms)),
} = {}) => {
  const deadline = now() + timeoutMs;
  let nextNotice = 0;
  for (;;) {
    if (tryAcquireLock(path, pid))
      return { path, pid, release: () => releaseLock(path, pid) };
    const holder = readLockPid(path);
    if (now() >= nextNotice) {
      onWait?.(holder);
      nextNotice = now() + noticeMs;
    }
    if (now() >= deadline) {
      throw new Error(
        "timed out waiting for the machine-wide E2E lock — " +
          `path=${path} waited_ms=${timeoutMs} holder_pid=${holder ?? "unknown"}`,
      );
    }
    await sleep(pollMs);
  }
};
