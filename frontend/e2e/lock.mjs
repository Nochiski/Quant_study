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

import {
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join } from "node:path";

export const LOCK_DIRECTORY_NAME = "quant-e2e.lock";
/**
 * 바깥에서 이미 같은 잠금을 쥐고 우리를 부른 경우 "1". 그때 또 잡으려 들면 자기 자신을 기다리는
 * 교착이 된다 — 바깥 주인 pid 는 살아 있으니 상한까지 그대로 멈춘다. 래퍼로 감싸 돌릴 때 쓴다.
 */
export const OUTER_LOCK_ENV = "QUANT_E2E_LOCK_HELD";
export const DEFAULT_LOCK_PATH = join(tmpdir(), LOCK_DIRECTORY_NAME);
export const PID_FILE_NAME = "pid";
/**
 * `mkdir` 직후 `pid` 를 쓰기 전 창의 유예. 그 창의 잠금은 "주인 없음"처럼 보이지만 사실 막
 * 잡히는 중이라, 이 시간이 지나야 죽은 것으로 본다. 유예가 없으면 옆 프로세스가 갓 잡힌 잠금을
 * 회수해 둘 다 주인이 된다(1차 리뷰 DEFECT-P105-002 경합 A).
 */
export const PID_GRACE_MS = 5_000;
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
 * 잠금 디렉터리가 만들어진 뒤 지난 시간(ms). 없으면 null.
 * @param {string} path
 */
const lockAgeMs = (path, now) => {
  try {
    return now - statSync(path).mtimeMs;
  } catch (error) {
    if (/** @type {NodeJS.ErrnoException} */ (error).code === "ENOENT")
      return null;
    throw error;
  }
};

/**
 * 죽은 주인의 잠금을 회수한다. 성공하면 true.
 *
 * 지우기 전에 **먼저 자리를 비킨다**: `rename` 은 한 프로세스만 성공하므로 둘이 같은 잠금을
 * 노려도 하나만 옮긴다. 곧장 `rmSync` 하면 진 쪽이 그 사이 새로 잡힌 잠금을 지운다.
 *
 * 비켜 놓고 한 번 더 확인하는 이유는, 우리가 "죽었다"고 판단한 **뒤** 남이 그 자리를 새로 잡았을
 * 수 있기 때문이다. 그때 옮긴 것은 죽은 잠금이 아니라 남의 **살아 있는** 잠금이라 되돌려 놓아야
 * 한다. 이 확인이 없으면 둘 다 주인이 된다(1차 리뷰 DEFECT-P105-002 경합 B).
 * @param {string} path
 * @param {number | null} deadPid 우리가 죽었다고 판단한 주인(pid 파일이 없었으면 null)
 * @param {number} pid
 */
const reclaimDeadLock = (path, deadPid, pid) => {
  const aside = `${path}.stale-${pid}-${Date.now()}`;
  try {
    renameSync(path, aside);
  } catch {
    // 이미 남이 비켰거나(ENOENT) 잡고 있으면(EPERM/EACCES/EBUSY) 회수는 그쪽 몫이다.
    return false;
  }
  const moved = readLockPid(aside);
  if (moved !== null && moved !== deadPid && isProcessAlive(moved)) {
    // 우리가 죽었다고 판단한 뒤 남이 새로 잡았다. 옮긴 건 **살아 있는** 잠금이니 돌려 놓는다.
    // pid 를 못 읽은 경우(null)는 돌려 놓지 않는다 — 옮기기 전에 이미 유예 규칙으로 죽은 잠금이라
    // 판정했고, 여기서 되돌리면 아무도 회수하지 못해 둘 다 빈손으로 끝난다.
    try {
      renameSync(aside, path);
    } catch {
      // 자리가 이미 다시 찼다 — 되돌릴 수 없으니 옮겨 둔 쪽만 치운다.
      rmSync(aside, { recursive: true, force: true });
    }
    return false;
  }
  rmSync(aside, { recursive: true, force: true });
  return true;
};

/**
 * pid 를 담은 잠금을 **통째로 만들어 자리에 올린다**. 올렸으면 true.
 *
 * `mkdir` 로 자리를 잡고 나중에 pid 를 쓰면 그 사이 잠금이 "pid 없는 상태"로 보이는 창이 생기고,
 * 회수 로직이 그 창을 죽은 잠금으로 오해한다. 먼저 딴 자리에 pid 까지 갖춘 디렉터리를 만들고
 * `rename` 한 번으로 올리면 그 창 자체가 없다 — 올라간 순간부터 pid 가 들어 있다.
 * 잡는 마지막 동작이 원자적 rename 하나라 "회수했다가 다시 mkdir" 사이의 창이 없다
 * (1차 리뷰 DEFECT-P105-002). 다만 대상이 **빈 디렉터리**일 때의 의미가 플랫폼마다 다르다
 * — Windows 는 실패하고 POSIX 는 교체에 성공한다. 그래서 호출자가 자리가 비었는지 먼저 보고
 * 부른다(`tryAcquireLock`). 여기서는 빈 자리를 동시에 노리는 둘 중 하나만 이긴다는 것만 맡는다.
 * @param {string} path
 * @param {number} pid
 */
const publishLock = (path, pid) => {
  const staging = `${path}.new-${pid}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  mkdirSync(staging);
  writeFileSync(join(staging, PID_FILE_NAME), String(pid));
  try {
    renameSync(staging, path);
    return true;
  } catch {
    rmSync(staging, { recursive: true, force: true });
    return false;
  }
};

/**
 * 한 번만 시도한다. 잡았으면 true, 살아 있는 주인이 있으면 false.
 * 죽은 주인의 잠금은 원자적으로 회수하고 한 번만 다시 올려 본다.
 * @param {string} path
 * @param {number} pid
 * @param {boolean} [reclaimed]
 * @param {number} [now]
 */
export const tryAcquireLock = (
  path,
  pid,
  reclaimed = false,
  now = Date.now(),
) => {
  // **자리가 비었을 때만** 올린다. POSIX `rename(2)` 는 대상이 빈 디렉터리면 성공하므로, 자리를
  // 보지 않고 올리면 다른 구현이 `mkdir` 로 막 잡고 pid 를 쓰기 전인 잠금을 덮어쓴다. 그러면 그
  // 구현의 `writeFileSync` 가 우리 디렉터리에 자기 pid 를 적어 둘 다 주인이 된다 — 잠금이 막으려던
  // 조용한 거짓 통과가 그 플랫폼에서만 되살아난다(2차 리뷰 DEFECT-P105R2-001).
  // 빈 자리를 둘이 동시에 노리는 경우는 그대로 `rename` 원자성이 가른다.
  if (lockAgeMs(path, now) === null && publishLock(path, pid)) {
    sweepLeftovers(path);
    return true;
  }
  if (reclaimed) return false;
  const holder = readLockPid(path);
  if (holder !== null && isProcessAlive(holder)) return false;
  if (holder === null) {
    // pid 가 없다 = 다른 구현이 `mkdir` 로 막 잡는 중이거나, 주인이 pid 를 쓰기 전에 죽었다.
    // 유예 안이면 앞의 경우로 보고 기다린다 — 갓 잡힌 잠금을 뺏지 않는다.
    const age = lockAgeMs(path, now);
    if (age !== null && age < PID_GRACE_MS) return false;
  }
  if (!reclaimDeadLock(path, holder, pid)) return false;
  const acquired = tryAcquireLock(path, pid, true, now);
  if (acquired) sweepLeftovers(path);
  return acquired;
};

/**
 * 내가 주인일 때만 지운다. 이미 회수돼 남이 잡고 있으면 건드리지 않는다.
 * @param {string} path
 * @param {number} pid
 */
/**
 * 앞선 실행이 남긴 `<lock>.new-*`·`<lock>.stale-*` 찌꺼기를 치운다.
 *
 * 올리거나 회수하는 중에 프로세스가 죽으면 그 임시 디렉터리가 남는다. 잠금 자체는 영향이 없지만
 * 치우는 곳이 없으면 임시 디렉터리에 쌓인다(2차 리뷰 P3-6).
 *
 * **오래된 것만** 치운다. 잠금을 쥐었다고 다른 프로세스가 아무 일도 안 하는 것은 아니다 —
 * 경합에서 진 쪽이 자기 `.stale-` 을 되돌리거나 `.new-` 를 정리하는 중일 수 있고, 그것을 지우면
 * 그 프로세스가 깨진다(실제로 이 규칙 없이 두 쪽이 서로의 임시 디렉터리를 지워 아무도 잠금을
 * 잡지 못했다). 한 번의 획득은 밀리초 단위라 한 시간을 넘긴 것은 확실히 버려진 것이다.
 * @param {string} path
 * @param {number} [olderThanMs]
 * @param {number} [now]
 */
export const LEFTOVER_MAX_AGE_MS = 60 * 60 * 1000;

export const sweepLeftovers = (
  path,
  olderThanMs = LEFTOVER_MAX_AGE_MS,
  now = Date.now(),
) => {
  const parent = dirname(path);
  const prefix = `${basename(path)}.`;
  let swept = 0;
  for (const name of readdirSync(parent)) {
    if (!name.startsWith(prefix)) continue;
    const suffix = name.slice(prefix.length);
    if (!suffix.startsWith("new-") && !suffix.startsWith("stale-")) continue;
    const leftover = join(parent, name);
    const age = lockAgeMs(leftover, now);
    if (age === null || age < olderThanMs) continue;
    rmSync(leftover, { recursive: true, force: true });
    swept += 1;
  }
  return swept;
};

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
 * @param {boolean} [options.outerHeld]
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
  outerHeld = process.env[OUTER_LOCK_ENV] === "1",
  timeoutMs = DEFAULT_TIMEOUT_MS,
  pollMs = DEFAULT_POLL_MS,
  noticeMs = DEFAULT_NOTICE_MS,
  onWait,
  now = Date.now,
  sleep = (ms) => new Promise((done) => setTimeout(done, ms)),
} = {}) => {
  if (outerHeld) {
    // 바깥이 이미 주인이다. 우리는 잡지도 놓지도 않는다 — 놓으면 바깥의 잠금을 뺏는 셈이 된다.
    return { path, pid, release: () => false };
  }
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
