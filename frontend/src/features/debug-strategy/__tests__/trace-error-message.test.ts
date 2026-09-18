import { describe, expect, it } from "vitest";

import { ApiRequestError } from "../../../shared/api";
import { traceErrorMessage } from "../model/use-strategy-trace";

describe("traceErrorMessage (Phase 1 감사 위험 5b)", () => {
  it("translates a known backend 422 code instead of exposing the raw detail", () => {
    const error = new ApiRequestError(
      "trace",
      422,
      "trace.strategy.requires_upgrade",
      "strategy revision 1 requires upgrade",
    );
    expect(traceErrorMessage(error)).toBe(
      "저장된 1.0 revision은 추적할 수 없습니다. 업그레이드 후 새 revision으로 저장하세요.",
    );
  });

  it("falls back to the detail, then the message, for codes without a translation", () => {
    expect(
      traceErrorMessage(new ApiRequestError("trace", 409, "trace.strategy.stale", "stale source")),
    ).toBe("stale source");
    expect(traceErrorMessage(new ApiRequestError("trace", 500))).toBe(
      new ApiRequestError("trace", 500).message,
    );
    expect(traceErrorMessage(new Error("boom"))).toBe("boom");
    expect(traceErrorMessage("plain")).toBe("plain");
  });
});
