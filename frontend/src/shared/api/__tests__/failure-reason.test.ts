import { describe, expect, it } from "vitest";

import { ApiRequestError, failureReason } from "../strategy-workbench";

// #270: 접힌 "서버 사유"에 둘 원문은 이 함수 하나가 고른다. 개발자 진단(`API request failed: …`)은 싣지 않는다.
describe("failureReason", () => {
  it("prefers the server detail, then the code-less 422 summary", () => {
    expect(
      failureReason(
        new ApiRequestError("upgrade", 422, "x.code", "stage=1.0 pointers=[]"),
      ),
    ).toBe("stage=1.0 pointers=[]");
    expect(
      failureReason(
        new ApiRequestError(
          "save",
          422,
          undefined,
          undefined,
          null,
          null,
          undefined,
          "title: Field required",
        ),
      ),
    ).toBe("title: Field required");
  });

  it("drops the developer message when the server sent no reason", () => {
    const error = new ApiRequestError("compileStrategyDocument", 500);
    expect(error.message).toContain("API request failed");
    expect(failureReason(error)).toBeNull();
  });

  it("keeps the message of a failure that never reached the server", () => {
    expect(failureReason(new TypeError("Failed to fetch"))).toBe(
      "Failed to fetch",
    );
    expect(failureReason("plain")).toBeNull();
  });
});
