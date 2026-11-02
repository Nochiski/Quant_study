import { describe, expect, it } from "vitest";

import { displayTime } from "../display-time";

describe("displayTime", () => {
  it("shows the instant in the given time zone instead of pinning UTC", () => {
    // 12:34Z 는 서울(UTC+9) 21:34 다. 예전에는 늘 UTC 로 보여 PC 시각과 9시간 어긋났다.
    const seoul = displayTime("2026-10-17T12:34:56Z", { timeZone: "Asia/Seoul" });
    expect(seoul).toMatch(/21:34|9:34/u);
    expect(seoul).not.toContain("UTC");
    expect(displayTime("2026-10-17T12:34:56Z", { timeZone: "UTC" })).toMatch(/12:34/u);
  });

  it("keeps a value it cannot read as the original text", () => {
    expect(displayTime("not-a-time")).toBe("not-a-time");
  });
});
