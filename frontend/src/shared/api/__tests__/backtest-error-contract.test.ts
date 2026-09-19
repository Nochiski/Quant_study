import { describe, expect, it } from "vitest";

import { tOptional } from "../../config";
import type {
  BacktestRunState,
  StartBacktestErrors,
} from "../generated/types.gen";

type StartBacktest422 = StartBacktestErrors[422];

const classify = (response: StartBacktest422): string => {
  const { detail } = response;
  if (Array.isArray(detail)) {
    return `request.validation:${detail[0]?.type ?? "unknown"}`;
  }
  switch (detail.code) {
    case "backtest.run.invalid":
      return `run:${detail.message}`;
    case "portfolio.strategy.invalid":
      return `strategy:${detail.validation.valid}`;
    // `portfolio.data.unavailable` · `portfolio.raw_observation.invalid` 는 시작 요청이 데이터를
    // 읽지 않게 되면서(이슈 #158) 이 경로의 계약에서 빠졌다. 그 실패는 run 상태 `error` 로 온다.
    case "backtest.strategy.requires_upgrade":
      return `upgrade:${detail.message}`;
    default: {
      const exhaustive: never = detail;
      return exhaustive;
    }
  }
};

type RunFailureCode = NonNullable<BacktestRunState["error_code"]>;

// backend `RunFailureCode` 어휘(OpenAPI enum → 생성 타입)와 run 페이지 번역 키의 동기화. 코드가 늘면 이 표가
// 타입 오류로 먼저 깨지고, 번역이 빠지면 아래 단언이 깨진다(이슈 #158).
const RUN_FAILURE_CODES: Record<RunFailureCode, true> = {
  "portfolio.strategy.invalid": true,
  "portfolio.data.unavailable": true,
  "portfolio.raw_observation.invalid": true,
  "backtest.run.invalid": true,
  "backtest.run.internal": true,
};

describe("backtest run failure code vocabulary", () => {
  it("has a translated recovery message for every run failure code", () => {
    for (const code of Object.keys(RUN_FAILURE_CODES)) {
      expect(tOptional(`backtest.run.error.${code}`), code).not.toBeNull();
    }
  });
});

describe("generated startBacktest error contract", () => {
  it("narrows malformed and coded 422 responses without a handwritten DTO", () => {
    expect(
      classify({
        detail: { code: "backtest.run.invalid", message: "missing strategy" },
      }),
    ).toBe("run:missing strategy");
    expect(
      classify({
        detail: [{ loc: ["body", "core"], msg: "invalid core", type: "enum" }],
      }),
    ).toBe("request.validation:enum");
    expect(
      classify({
        detail: {
          code: "portfolio.strategy.invalid",
          validation: { valid: false, issues: [] },
        },
      }),
    ).toBe("strategy:false");
    expect(
      classify({
        detail: {
          code: "backtest.strategy.requires_upgrade",
          message: "schema 1.0 revision",
        },
      }),
    ).toBe("upgrade:schema 1.0 revision");
  });
});
