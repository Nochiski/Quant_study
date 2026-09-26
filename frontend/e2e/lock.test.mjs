/**
 * E2E 잠금·포트 유틸 단위 테스트.
 *
 * 이 잠금이 지키는 것은 "한 머신에서 e2e 가 한 번에 하나만 돈다"는 불변식이다. 깨지면 브라우저가
 * 옆 워크트리의 backend 를 테스트하고도 초록으로 통과한다 — 그래서 회수 규칙까지 테스트로 고정한다.
 *
 * 형식(디렉터리 + 십진수 `pid` 파일)은 한 머신의 다른 구현과 같은 잠금을 공유하려고 맞춘 것이라
 * 그 자체가 계약이다. 모양이 달라지면 서로를 주인으로 못 알아보고 둘 다 돌아 버린다.
 */

import { spawn } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  readdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  utimesSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { readFile } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import {
  DEFAULT_LOCK_PATH,
  LOCK_DIRECTORY_NAME,
  PID_FILE_NAME,
  acquireLock,
  isProcessAlive,
  readLockPid,
  releaseLock,
  sweepLeftovers,
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
import { assertPortsFree, isPortFree } from "./free-port.mjs";
import { describePortOwner, listenerPid } from "./port-owner.mjs";

// vitest 의 `import.meta.url` 은 dev 서버 http URL 이라 자식 노드가 불러오지 못한다.
// 자식은 진짜 파일을 봐야 하므로 작업 디렉터리(= frontend) 기준 file URL 로 만든다.
/** 주석을 지운 코드만 남긴다 — 사유를 적은 주석이 단언에 걸리지 않게. */
const stripComments = (source) =>
  source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

const lockModuleUrl = pathToFileURL(resolve("e2e/lock.mjs")).href;

/**
 * 자식 노드 프로세스로 잠금을 노려 본다.
 *
 * 결과는 시도 **직후** IPC 메시지로 오고, 자식은 부모가 `release()` 로 놓으라고 할 때까지 산다.
 * 주인의 생존 구간을 시간이 아니라 신호로 정하는 것이 요점이다 — 고정 시간만 살려 두면 부하가
 * 걸린 러너(2코어 CI)에서 늦은 자식이 이미 끝난 주인의 잠금을 **정당하게** 회수해 승자가 둘이
 * 되고, 잠금은 멀쩡한데 테스트만 간헐적으로 붉어진다(3차 리뷰 DEFECT-P105R3-001).
 */
const runRacer = (source) => {
  const child = spawn(process.execPath, ["--input-type=module", "-e", source], {
    stdio: ["ignore", "ignore", "pipe", "ipc"],
  });
  let err = "";
  child.stderr.on("data", (chunk) => (err += chunk));
  const reported = new Promise((done, fail) => {
    child.once("message", done);
    child.once("error", fail);
    child.once("exit", (code) =>
      fail(new Error(`racer exited ${code} before reporting: ${err}`)),
    );
  });
  const finished = new Promise((done, fail) => {
    child.once("error", fail);
    child.once("exit", (code) =>
      code === 0
        ? done(undefined)
        : fail(new Error(`racer exited ${code}: ${err}`)),
    );
  });
  // 한쪽이 실패해 다른 쪽을 await 하지 못하고 빠져나가도 처리 안 된 거부로 새지 않게 한다.
  // 원래 promise 를 돌려주므로 await 하는 쪽은 그대로 거부를 받는다.
  reported.catch(() => {});
  finished.catch(() => {});
  return {
    reported,
    finished,
    release: () => {
      if (child.connected) child.send("release");
    },
  };
};

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

/**
 * 잠금이 유예를 넘기도록 디렉터리 시각을 과거로 돌린다.
 *
 * `now` 인자를 앞당기는 대신 파일시스템 시각을 직접 고치는 이유는, 앞당긴 시각이 실제 mtime 과
 * 맞물리는 방식이 플랫폼마다 달라 테스트가 흔들리기 때문이다. 여기서는 유예가 지났다는 사실만
 * 필요하다.
 */
const age = (path, ms = 2 * 60 * 60 * 1000) => {
  const past = (Date.now() - ms) / 1000;
  utimesSync(path, past, past);
};

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

  it("갓 만들어진 pid 없는 잠금은 뺏지 않고 기다린다", () => {
    // 경합 A(1차 리뷰 DEFECT-P105-002): 남이 `mkdir`를 막 통과하고 `pid`를 쓰기 전 창이다.
    // 이걸 "주인 없음"으로 보고 회수하면 둘 다 주인이 된다.
    const path = lockPath();
    mkdirSync(path);

    expect(readLockPid(path)).toBeNull();
    expect(tryAcquireLock(path, process.pid)).toBe(false);
  });

  it("자리가 차 있으면 올리기를 시도조차 하지 않는다", () => {
    // 플랫폼 무관 단언. POSIX `rename(2)`는 대상이 **빈 디렉터리면 성공**하므로, 자리를 보지 않고
    // 올리면 남이 `mkdir`로 막 잡고 pid를 쓰기 전인 잠금을 덮어쓴다(2차 리뷰 DEFECT-P105R2-001).
    // Windows 는 그 rename 이 실패해 증상이 가려지므로, "시도 자체를 안 한다"를 직접 고정한다.
    const path = lockPath();
    mkdirSync(path);
    const before = statSync(path).mtimeMs;

    expect(tryAcquireLock(path, process.pid)).toBe(false);

    // 갓 만들어진 남의 자리를 건드리지 않았다: 그대로 있고, 비어 있고, 시각도 그대로다.
    expect(existsSync(path)).toBe(true);
    expect(readdirSync(path)).toEqual([]);
    expect(statSync(path).mtimeMs).toBe(before);
    // 올리려다 만 staging 찌꺼기도 남기지 않는다.
    expect(
      readdirSync(dirname(path)).filter((name) => name.includes(".new-")),
    ).toEqual([]);
  });

  it("유예를 넘긴 pid 없는 잠금은 회수한다", () => {
    // 주인이 pid를 쓰기 전에 죽은 경우다. 영원히 막히면 안 된다.
    const path = lockPath();
    mkdirSync(path);
    age(path);

    expect(tryAcquireLock(path, process.pid)).toBe(true);
    expect(readLockPid(path)).toBe(process.pid);
  });

  it("pid가 숫자가 아니면 주인 없는 잠금으로 보고 회수한다", () => {
    const broken = lockPath();
    mkdirSync(broken);
    writeFileSync(join(broken, PID_FILE_NAME), "not a pid");
    age(broken);

    expect(readLockPid(broken)).toBeNull();
    expect(tryAcquireLock(broken, process.pid)).toBe(true);
  });

  it("오래 남은 임시 디렉터리만 치운다", () => {
    // 진행 중인 남의 임시 디렉터리를 지우면 그 프로세스가 깨진다 — 실제로 둘이 서로의 것을 지워
    // 아무도 못 잡는 일이 있었다. 그래서 나이로 가린다(2차 리뷰 P3-6).
    const path = lockPath();
    const parent = dirname(path);
    const stale = join(parent, `${basename(path)}.stale-1-1`);
    const fresh = join(parent, `${basename(path)}.new-2-2`);
    const unrelated = join(parent, "something-else");
    for (const directory of [stale, fresh, unrelated]) mkdirSync(directory);
    age(stale);

    expect(sweepLeftovers(path)).toBe(1);

    expect(existsSync(stale)).toBe(false);
    expect(existsSync(fresh)).toBe(true);
    expect(existsSync(unrelated)).toBe(true);
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

describe("E2E 잠금 경합 (두 프로세스)", () => {
  /** 죽은 주인의 잠금을 동시에 노리는 자식 하나. 시도 결과를 알리고, 놓으라 할 때까지 쥔다. */
  const racer = (path, startAt) => `
    import { tryAcquireLock, readLockPid } from ${JSON.stringify(lockModuleUrl)};
    const path = ${JSON.stringify(path)};
    const sleep = (ms) =>
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
    while (Date.now() < ${startAt}) {}
    // 실제 러너처럼 자리가 날 때까지 다시 시도한다. 한 번만 보면 Windows 에서 두 회수 rename 이
    // 서로가 연 pid 파일 핸들에 막혀 **둘 다** 실패하는 찰나가 잡힌다(EPERM). 그건 주인이 둘이
    // 되는 위험이 아니라 그 순간 아무도 못 잡은 것이고, 러너는 폴링으로 다시 온다.
    let won = false;
    for (let attempt = 0; attempt < 300 && !won; attempt += 1) {
      won = tryAcquireLock(path, process.pid);
      if (won) break;
      // 살아 있는 남의 잠금이 자리를 지키면 정당하게 진 것이다. 죽은 주인이 그대로면 다시 시도한다.
      const holder = readLockPid(path);
      if (holder !== null && holder !== ${DEAD_PID}) break;
      sleep(5);
    }
    // 이긴 쪽은 실제 러너처럼 잠금을 쥔 채 산다. 곧바로 죽으면 진 쪽이 그 잠금을 정당하게
    // 회수하므로 "둘 다 이겼다"가 되는데, 그건 잠금의 결함이 아니라 모델이 틀린 것이다.
    // 얼마나 사는지는 부모가 정한다 — 두 시도가 모두 끝난 뒤에 놓으라고 알려 온다.
    // 진 쪽도 같이 기다린다. 보내자마자 exit 하면 아직 채널에 남은 결과가 버려질 수 있다.
    process.on("message", () => process.exit(0));
    // 부모가 먼저 죽으면 채널이 닫힌다. 잠금을 쥔 고아로 남지 않게 같이 끝낸다.
    process.on("disconnect", () => process.exit(0));
    process.send({ won, pid: process.pid, holder: readLockPid(path) });
  `;

  it("죽은 주인의 잠금을 둘이 동시에 노려도 하나만 이긴다", async () => {
    // 경합 B(1차 리뷰 DEFECT-P105-002): 회수와 재획득이 원자적이지 않으면 둘 다 true 를 받고,
    // 진 쪽이 이긴 쪽의 **살아 있는** 잠금을 지운다. 잠금이 막으려던 바로 그 조용한 거짓 통과다.
    for (let round = 0; round < 8; round += 1) {
      const path = lockPath();
      mkdirSync(path);
      // 유예를 넘긴 죽은 주인으로 만든다.
      writeFileSync(join(path, PID_FILE_NAME), String(DEAD_PID));
      age(path);

      const startAt = Date.now() + 200;
      const racers = [
        runRacer(racer(path, startAt)),
        runRacer(racer(path, startAt)),
      ];
      let results;
      let holder;
      try {
        results = await Promise.all(racers.map((one) => one.reported));
        // 두 시도가 모두 끝난 지금 주인을 본다. 이긴 쪽은 아직 살아 있으므로 진 쪽에게 이 잠금은
        // 회수 대상이 아니었다 — 단언이 자식의 스케줄링과 무관해진다.
        holder = readLockPid(path);
      } finally {
        // 한쪽이 보고 없이 죽어도 남은 쪽이 잠금을 쥔 채 테스트 끝까지 매달리지 않게 놓아 준다.
        for (const one of racers) one.release();
      }
      await Promise.all(racers.map((one) => one.finished));

      const winners = results.filter((result) => result.won);
      // 깨졌을 때 두 자식이 무엇을 봤는지 없으면 원인을 못 찾는다.
      const seen = `${JSON.stringify(results)} holder=${holder}`;
      expect(winners, seen).toHaveLength(1);
      // 이긴 쪽의 잠금이 그대로 남아 있어야 한다 — 진 쪽이 지우고 가면 안 된다.
      expect(holder, seen).toBe(winners[0].pid);
    }
  }, 120_000);
});

describe("포트를 쥔 프로세스", () => {
  it("리스너의 pid·시작 시각·커맨드를 알아낸다", async (context) => {
    // 잠금은 자기 잠금의 stale만 회수한다. Playwright만 죽고 uvicorn·vite preview가 남으면 그
    // 고아가 포트를 쥔 채 남는데, "쓰이는 중"이라는 사실만으로는 고아인지 남의 정상 실행인지
    // 구별할 수 없다. 사람이 판단할 수 있게 주인을 찍어 준다.
    const { createServer } = await import("node:net");
    const server = createServer();
    await new Promise((done) =>
      server.listen({ port: 0, host: "127.0.0.1" }, () => done(undefined)),
    );
    const address = server.address();
    const port =
      typeof address === "object" && address !== null ? address.port : 0;

    const owner = describePortOwner(port);
    if (owner === null) {
      // 조회는 최선껏이 모듈 계약이다 — OS 도구(`lsof` 등)가 없거나 시간을 넘긴 러너에서는 null
      // 이 정상 결과라, 여기서 붉어지면 모듈이 아니라 러너 이미지를 탓하게 된다(3차 리뷰 P3 추가 1).
      await new Promise((done) => server.close(() => done(undefined)));
      context.skip();
      return;
    }

    expect(owner.pid).toBe(process.pid);
    // 시작 시각·커맨드는 최선껏이라 없을 수 있지만, 있으면 한 줄에 들어갈 길이여야 한다.
    expect(owner?.command === null || owner.command.length <= 161).toBe(true);
    expect(owner?.command ?? "").not.toContain(String.fromCharCode(10));

    await new Promise((done) => server.close(() => done(undefined)));
    expect(describePortOwner(port)).toBeNull();
  }, 30_000);

  it("막힌 포트 오류가 주인과 확인 방법을 담는다", async () => {
    const { createServer } = await import("node:net");
    const server = createServer();
    await new Promise((done) =>
      server.listen({ port: 0, host: "127.0.0.1" }, () => done(undefined)),
    );
    const address = server.address();
    const port =
      typeof address === "object" && address !== null ? address.port : 0;

    const failure = await assertPortsFree([
      { port, label: "backend", env: "PW_BACKEND_PORT" },
    ]).catch((error) => error);

    expect(failure).toBeInstanceOf(Error);
    // 주인 조회는 최선껏이다. 도구가 없는 러너에서는 "모른다"로 내려앉는 것이 계약이라 그쪽을
    // 본다 — 조회 성공을 전제하면 degrade 가 아니라 실패가 된다(3차 리뷰 P3 추가 1).
    expect(failure.message).toContain(
      listenerPid(port) === null ? "owner unknown" : `pid=${process.pid}`,
    );
    // 할 일이 먼저, 고아일 가능성은 그 뒤다 — 옆 체크아웃의 정상 dev 서버도 이 경로로 온다.
    const stopAt = failure.message.indexOf("Stop the other server");
    expect(stopAt).toBeGreaterThan(-1);
    expect(failure.message.indexOf("own ports")).toBeGreaterThan(stopAt);
    expect(failure.message.indexOf("orphan")).toBeGreaterThan(stopAt);
    // 자동으로 죽이지 않는다는 사실이 메시지에 있어야 한다.
    expect(failure.message).toContain("never kills a process it did not start");

    await new Promise((done) => server.close(() => done(undefined)));
  }, 30_000);
});

describe("E2E 포트 타입 선언", () => {
  it("`ports.d.mts`가 `ports.mjs`의 export를 빠짐없이 비춘다", async () => {
    // `tsconfig.playwright.json`이 `e2e/**/*.ts`만 보므로 `.mjs`는 타입 검사 밖이고, 손으로
    // 유지하는 거울이 어긋나도 아무 게이트가 잡지 못한다(1차 리뷰 P3-7). 이 테스트가 그 역할이다.
    const [runtime, declared] = await Promise.all([
      import("./ports.mjs"),
      readFile(resolve("e2e/ports.d.mts"), "utf8"),
    ]);
    const declaredNames = new Set(
      [...declared.matchAll(/export declare const (\w+)/g)].map(
        (match) => match[1],
      ),
    );

    expect(declaredNames).toEqual(new Set(Object.keys(runtime)));
  });
});

describe("E2E 포트가 단위 실행에 새지 않는다", () => {
  it("vite 설정이 주변 환경의 PW_* 를 읽지 않는다", async () => {
    // `PW_BACKEND_PORT` 를 셸에 export 해 두는 것은 README 가 권하는 워크트리 운용이다. 그 값이
    // vite 설정으로 새면 vitest·dev 가 쓰는 SDK 기본 주소까지 바뀌어, MSW 핸들러가 등록된 주소와
    // 어긋나 단위 테스트가 통째로 깨진다(1차 리뷰 DEFECT-P105-003).
    // 주석에는 이 변수 이름이 사유로 적혀 있으므로 코드만 본다.
    const code = stripComments(
      await readFile(resolve("vite.config.ts"), "utf8"),
    );

    expect(code).not.toMatch(/PW_BACKEND_PORT|PW_PREVIEW_PORT/);
    expect(code).not.toMatch(/ports\.mjs/);
    expect(code).not.toMatch(/VITE_API_BASE_URL/);
  });

  it("빌드 자식이 받는 주소는 포트 설정을 따른다", async () => {
    // 서식이 아니라 값을 본다 — Prettier 가 줄을 바꿔도 깨지지 않게(2차 리뷰 P3-5).
    const { BUILD_ARGS, buildEnv } = await import("./build-command.mjs");

    expect(BUILD_ARGS).toEqual(["run", "build"]);
    expect(buildEnv({})).toEqual({
      VITE_API_BASE_URL: `http://localhost:${DEFAULT_BACKEND_PORT}`,
    });
    expect(buildEnv({ [BACKEND_PORT_ENV]: "18000" })).toEqual({
      VITE_API_BASE_URL: "http://localhost:18000",
    });
    // 직접 준 주소가 있으면 그 뜻을 덮지 않는다.
    expect(
      buildEnv({
        VITE_API_BASE_URL: "http://localhost:19999",
        [BACKEND_PORT_ENV]: "18000",
      }),
    ).toEqual({ VITE_API_BASE_URL: "http://localhost:19999" });
  });

  it("러너는 그 주소를 빌드 자식에만 넘긴다", async () => {
    // 3차 리뷰 P3-2: 이름은 "빌드 자식에만"인데 단언은 `BUILD_ARGS` 만 보고 있었다. 주소를 만드는
    // 곳이 `buildEnv` 하나이므로, 러너가 그것을 빌드 호출에만 쓰고 Playwright 자식 env 에는
    // 아무 주소도 끼워 넣지 않는다는 것을 본다. 이쪽이 새면 브라우저가 아니라 Playwright 프로세스
    // 환경이 오염돼, `vite.config.ts` 단언(반대쪽)은 그대로 통과한다.
    const runner = stripComments(
      await readFile(resolve("e2e/run-playwright.mjs"), "utf8"),
    );

    // 주소 문자열을 러너가 직접 만들지 않는다.
    expect(runner).not.toMatch(/VITE_API_BASE_URL|backendOrigin/);
    // 빌드 자식 한 번에만 쓴다.
    expect(runner.match(/buildEnv\(\)/g)).toHaveLength(1);
    expect(runner).toMatch(
      /runToCompletion\(\s*"npm",\s*BUILD_ARGS,\s*buildEnv\(\)/,
    );
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
