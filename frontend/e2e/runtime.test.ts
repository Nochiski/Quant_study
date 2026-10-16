import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RUNTIME_DIRECTORY_ENV, runtimeContinuousDatabasePath } from "./runtime";

afterEach(() => vi.unstubAllEnvs());

describe("연속 계좌 E2E 상태 격리", () => {
  it("주변 기본 DB 설정이 있어도 서버에는 현재 실행 안의 별도 DB 경로만 넘긴다", async () => {
    const isolated = join(tmpdir(), "quant-continuous-runtime-contract");
    vi.stubEnv(RUNTIME_DIRECTORY_ENV, isolated);
    vi.stubEnv("STRATEGY_WORKBENCH_CONTINUOUS_DB_PATH", join(tmpdir(), "developer-continuous.sqlite3"));
    const { default: config } = await import("../playwright.config");
    if (!Array.isArray(config.webServer)) throw new Error("격리된 서버 설정 목록이 필요합니다.");
    const env = config.webServer[0].env!;
    const path = env.STRATEGY_WORKBENCH_CONTINUOUS_DB_PATH;
    expect(path).toBe(runtimeContinuousDatabasePath());
    expect(dirname(path)).toBe(isolated);
    expect(path).not.toBe(env.STRATEGY_WORKBENCH_RESEARCH_DB_PATH);
    expect(path).not.toBe(env.STRATEGY_WORKBENCH_DB_PATH);
  });
});
