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

  it("interpolates the backend detail into translations that keep a {detail} slot (backlog 17)", () => {
    expect(
      traceErrorMessage(
        new ApiRequestError("trace", 422, "trace.request.invalid", "as_of must be a session"),
      ),
    ).toBe("추적 요청이 올바르지 않습니다: as_of must be a session");
    expect(
      traceErrorMessage(new ApiRequestError("trace", 409, "trace.strategy.stale", "stale source")),
    ).toBe("편집 중인 문서가 저장본과 달라져 추적할 수 없습니다. 저장하거나 저장본을 다시 여세요.");
  });

  it("falls back to the detail, then the message, for codes without a translation", () => {
    expect(
      traceErrorMessage(new ApiRequestError("trace", 418, "trace.unknown", "teapot")),
    ).toBe("teapot");
    expect(traceErrorMessage(new ApiRequestError("trace", 500))).toBe(
      new ApiRequestError("trace", 500).message,
    );
    expect(traceErrorMessage(new Error("boom"))).toBe("boom");
    expect(traceErrorMessage("plain")).toBe("plain");
  });
});
