import { describe, expect, it } from "vitest";

import type { StartBacktestErrors } from "../generated/types.gen";

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
    case "portfolio.data.unavailable":
      return `data:${detail.status}`;
    default: {
      const exhaustive: never = detail;
      return exhaustive;
    }
  }
};

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
  });
});
