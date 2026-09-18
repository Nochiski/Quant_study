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
    // `trace.engine.incompatible`의 detail에는 message가 없다 — 고정 문장이며 내부 디버그 문자열이 새지 않는다(#149 P2-1).
    const incompatible = traceErrorMessage(
      new ApiRequestError("traceStrategy", 422, "trace.engine.incompatible"),
    );
    expect(incompatible).toBe("선택한 실행 엔진이 이 전략을 추적할 수 없습니다. 다른 실행 core를 고르세요.");
    expect(incompatible).not.toContain("API request failed");
    // 슬롯이 있는 코드에 detail이 빠져 와도 디버그 문자열 대신 슬롯을 비운다.
    expect(
      traceErrorMessage(new ApiRequestError("traceStrategy", 422, "trace.request.invalid")),
    ).toBe("추적 요청이 올바르지 않습니다.");
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
