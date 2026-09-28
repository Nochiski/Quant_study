import { describe, expect, it } from "vitest";

import { requestValidationSummary } from "../strategy-workbench";

// 이슈 #260: FastAPI 기본 422 는 `detail` 이 배열이라 `code`·`message` 를 꺼내지 못하고 서버 사유를 버렸다.
// 코드화된 계약이 없는 라우트를 위해 배열 detail 에서도 진단 문장을 만든다.
describe("requestValidationSummary", () => {
  it("names the first issue with its body path and the remaining count", () => {
    expect(
      requestValidationSummary({
        detail: [
          {
            type: "value_error",
            loc: ["body", "environment"],
            msg: "Value error, run environment value is out of range",
          },
          {
            type: "int_parsing",
            loc: ["body", "annualization_days"],
            msg: "Input should be a valid integer",
          },
        ],
      }),
    ).toBe(
      "environment: Value error, run environment value is out of range (+1)",
    );
    expect(
      requestValidationSummary({
        detail: [
          {
            type: "value_error",
            loc: ["body"],
            msg: "Value error, initial_cash must be positive",
          },
        ],
      }),
    ).toBe("body: Value error, initial_cash must be positive");
  });

  it("stays silent for coded or empty details", () => {
    expect(
      requestValidationSummary({
        detail: { code: "backtest.run.invalid", message: "x" },
      }),
    ).toBeUndefined();
    expect(requestValidationSummary({ detail: [] })).toBeUndefined();
    expect(requestValidationSummary("boom")).toBeUndefined();
  });
});
