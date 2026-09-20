import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { assertPortsFree } from "./free-port.mjs";
import { acquireLock } from "./lock.mjs";
import {
  BACKEND_PORT_ENV,
  PREVIEW_PORT_ENV,
  backendPort,
  previewPort,
} from "./ports.mjs";

const PREFIX = "quant-strategy-workbench-e2e-";
const tempRoot = resolve(tmpdir());
const runtimeDirectory = mkdtempSync(join(tempRoot, PREFIX));
const resolvedRuntime = resolve(runtimeDirectory);

const assertOwnedRuntime = () => {
  if (
    dirname(resolvedRuntime) !== tempRoot ||
    !basename(resolvedRuntime).startsWith(PREFIX)
  ) {
    throw new Error(`Refusing to clean unowned E2E path: ${resolvedRuntime}`);
  }
};

assertOwnedRuntime();
const ownDirectory = dirname(fileURLToPath(import.meta.url));

// 머신 단위 직렬화. 다른 워크트리가 돌고 있으면 기다린다 — 겹쳐 돌면 Playwright 가 남의 backend 를
// 우리 것으로 알고 진행한다(`lock.mjs` 머리말).
const lock = await acquireLock({
  onWait: (holder) =>
    console.log(`[e2e-lock] waiting: held by pid ${holder ?? "?"}`),
});
console.log(`[e2e-lock] acquired (pid ${lock.pid})`);

// 잠금을 잡고도 포트가 막혀 있으면(옆 체크아웃의 개발 서버 등) 남의 서버를 조용히 쓰지 않고 멈춘다.
try {
  await assertPortsFree([
    { port: backendPort(), label: "backend", env: BACKEND_PORT_ENV },
    { port: previewPort(), label: "preview", env: PREVIEW_PORT_ENV },
  ]);
} catch (error) {
  lock.release();
  assertOwnedRuntime();
  rmSync(resolvedRuntime, { recursive: true, force: true });
  throw error;
}
const playwrightCli = resolve(
  ownDirectory,
  "../node_modules/@playwright/test/cli.js",
);
const child = spawn(
  process.execPath,
  [playwrightCli, "test", ...process.argv.slice(2)],
  {
    cwd: resolve(ownDirectory, ".."),
    env: {
      ...process.env,
      STRATEGY_WORKBENCH_E2E_RUNTIME_DIR: resolvedRuntime,
    },
    stdio: "inherit",
    windowsHide: true,
  },
);

let interrupted = false;
const forward = (signal) => {
  interrupted = true;
  if (!child.killed) child.kill(signal);
};
process.once("SIGINT", () => forward("SIGINT"));
process.once("SIGTERM", () => forward("SIGTERM"));

let outcome;
try {
  outcome = await new Promise((resolveOutcome, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => resolveOutcome({ code, signal }));
  });
} finally {
  lock.release();
  console.log("[e2e-lock] released");
  assertOwnedRuntime();
  rmSync(resolvedRuntime, {
    recursive: true,
    force: false,
    maxRetries: 20,
    retryDelay: 100,
  });
  if (existsSync(resolvedRuntime)) {
    throw new Error(`E2E runtime cleanup failed: ${resolvedRuntime}`);
  }
}

if (interrupted || outcome.signal !== null) process.exitCode = 130;
else process.exitCode = outcome.code ?? 1;
