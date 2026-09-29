import { describe, expect, it } from "vitest";

import { ApiRequestError } from "../../../shared/api";
import { traceErrorMessage } from "../model/use-strategy-trace";

const GENERIC = "서버가 추적 요청을 처리하지 못했습니다. 잠시 뒤 다시 추적하세요.";

describe("traceErrorMessage (Phase 1 감사 위험 5b)", () => {
  it("translates a known backend 422 code instead of exposing the raw detail", () => {
    const error = new ApiRequestError(
      "trace",
      422,
      "trace.strategy.requires_upgrade",
      "strategy revision 1 requires upgrade",
    );
    expect(traceErrorMessage(error)).toBe(
      "저장된 이전 schema revision은 추적할 수 없습니다. 업그레이드 후 새 revision으로 저장하세요.",
    );
    expect(
      traceErrorMessage(new ApiRequestError("trace", 409, "trace.strategy.stale", "stale source")),
    ).toBe("편집 중인 문서가 저장본과 달라져 추적할 수 없습니다. 저장하거나 저장본을 다시 여세요.");
  });

  it("keeps the backend detail out of the body even for codes that name a field (#270)", () => {
    // 어느 칸인지는 접힌 서버 사유(`failureReason`)가 보인다 — 본문에 원문을 넣던 `{detail}` 자리는 없다.
    const invalid = traceErrorMessage(
      new ApiRequestError("trace", 422, "trace.request.invalid", "as_of must be a session"),
    );
    expect(invalid).toBe(
      "추적 요청이 올바르지 않습니다. 어느 칸이 틀렸는지는 서버 사유를 보세요.",
    );
    expect(invalid).not.toContain("as_of");
    const incompatible = traceErrorMessage(
      new ApiRequestError("traceStrategy", 422, "trace.engine.incompatible"),
    );
    expect(incompatible).toBe("선택한 실행 엔진이 이 전략을 추적할 수 없습니다. 다른 실행 core를 고르세요.");
  });

  it("falls back to a generic sentence, never the detail or the developer message", () => {
    expect(
      traceErrorMessage(new ApiRequestError("trace", 418, "trace.unknown", "teapot")),
    ).toBe(GENERIC);
    const serverError = traceErrorMessage(new ApiRequestError("trace", 500));
    expect(serverError).toBe(GENERIC);
    expect(serverError).not.toContain("API request failed");
    expect(traceErrorMessage(new Error("boom"))).toBe(GENERIC);
    expect(traceErrorMessage("plain")).toBe(GENERIC);
  });
});
