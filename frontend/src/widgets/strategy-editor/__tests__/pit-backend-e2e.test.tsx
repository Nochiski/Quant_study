import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { rmSync } from "node:fs";
import { platform, tmpdir } from "node:os";
import { join } from "node:path";
import { cwd, env, pid } from "node:process";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, beforeAll, expect, test } from "vitest";

import { configureStrategyWorkbenchApi } from "../../../shared/api";
import { StrategyEditor } from "../ui/strategy-editor";

const backendDirectory = join(cwd(), "..", "backend");
const pythonExecutable = join(
  backendDirectory,
  ".venv",
  platform() === "win32" ? "Scripts/python.exe" : "bin/python",
);
const port = 42_000 + (pid % 1_000);
const baseUrl = `http://127.0.0.1:${port}`;
const strategyDatabase = join(tmpdir(), `strategy-workbench-pit-${pid}-${Date.now()}.sqlite3`);
let backend: ChildProcessWithoutNullStreams;
let backendOutput = "";

const waitForBackend = async (): Promise<void> => {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(`${baseUrl}/api/v1/health`);
      if (response.ok) {
        return;
      }
    } catch {
      // The process needs a few scheduler turns before accepting connections.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(
    `real backend did not become ready — base_url=${baseUrl} output=${backendOutput}`,
  );
};

beforeAll(async () => {
  backend = spawn(
    pythonExecutable,
    [
      "-m",
      "uvicorn",
      "strategy_workbench.bootstrap.facade.http:build_runtime_http_app",
      "--factory",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--log-level",
      "warning",
    ],
    {
      cwd: backendDirectory,
      env: { ...env, STRATEGY_WORKBENCH_DB_PATH: strategyDatabase },
    },
  );
  backend.stdout.on("data", (chunk: Buffer) => {
    backendOutput += chunk.toString();
  });
  backend.stderr.on("data", (chunk: Buffer) => {
    backendOutput += chunk.toString();
  });
  configureStrategyWorkbenchApi(baseUrl);
  await waitForBackend();
}, 15_000);

afterAll(async () => {
  if (backend.exitCode === null) {
    backend.kill();
    await new Promise<void>((resolve) => {
      backend.once("exit", () => resolve());
    });
  }
  for (const suffix of ["", "-shm", "-wal"]) {
    rmSync(`${strategyDatabase}${suffix}`, { force: true });
  }
});

test("UI preview가 실제 backend mock의 공개 전 consensus revision을 숨긴다", async () => {
  const user = userEvent.setup();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <StrategyEditor />
    </QueryClientProvider>,
  );

  await user.click(await screen.findByRole("button", { name: "1 데이터" }));
  await user.clear(screen.getByLabelText("시작일"));
  await user.type(screen.getByLabelText("시작일"), "2024-01-04");
  await user.clear(screen.getByLabelText("종료일"));
  await user.type(screen.getByLabelText("종료일"), "2024-01-04");
  await user.click(
    await screen.findByRole("checkbox", { name: /12개월 선행 EPS/ }),
  );
  const previewButton = screen.getByRole("button", {
    name: "데이터 미리보기",
  });
  await waitFor(() => expect(previewButton).toBeEnabled());
  await user.click(previewButton);
  await user.click(
    await screen.findByRole("button", {
      name: "위험을 확인하고 미리보기",
    }),
  );

  expect(await screen.findByText("5,000")).toBeInTheDocument();
  expect(screen.queryByText("5,400")).not.toBeInTheDocument();
  expect(screen.getByText("2024-01-03")).toBeInTheDocument();
}, 15_000);
