import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

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
