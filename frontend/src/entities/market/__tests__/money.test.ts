import { describe, expect, it } from "vitest";

import { shortWon } from "..";

const EOK = 100_000_000;
const JO = 10_000 * EOK;

describe("shortWon", () => {
  it("reads jo once the amount rounds to 10,000 eok, like the backend summary sentence", () => {
    expect(shortWon(9_999 * EOK)).toBe("9,999억");
    // 9,999.5억은 억으로 반올림하면 1조다 — "10,000억"이 아니다
    expect(shortWon(9_999.5 * EOK)).toBe("1.00조");
    expect(shortWon(-1.4542 * JO)).toBe("1.45조");
  });

  it("switches to one decimal when two decimals would round up to 10 jo", () => {
    expect(shortWon(9.994 * JO)).toBe("9.99조");
    expect(shortWon(9.996 * JO)).toBe("10.0조");
    expect(shortWon(17.9349 * JO)).toBe("17.9조");
  });

  it("does not round a small non-zero amount to zero", () => {
    expect(shortWon(30_000_000)).toBe("1억 미만");
    expect(shortWon(0)).toBe("0억");
  });
});
