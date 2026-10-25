import { describe, expect, it } from "vitest";

import { periodBar, sessionPosition } from "../model/holdings-layout";

describe("sessionPosition", () => {
  const sessions = ["2024-01-02", "2024-02-01", "2024-03-04"];

  it("picks the latest trading day on or before a chosen day", () => {
    expect(sessionPosition(sessions, "2024-02-01")).toBe(1);
    expect(sessionPosition(sessions, "2024-02-20")).toBe(1);
    expect(sessionPosition(sessions, "2024-12-31")).toBe(2);
    expect(sessionPosition(sessions, "2023-12-29")).toBe(0);
  });
});

describe("periodBar", () => {
  it("places inclusive periods on the run's calendar span", () => {
    // 1/1~1/10 의 10일. 1/1 하루는 맨 앞 10%, 1/6~1/10 은 뒤 절반이다.
    expect(periodBar({ start: "2024-01-01", end: "2024-01-01" }, "2024-01-01", "2024-01-10")).toEqual({
      left: 0,
      width: 10,
    });
    expect(periodBar({ start: "2024-01-06", end: "2024-01-10" }, "2024-01-01", "2024-01-10")).toEqual({
      left: 50,
      width: 50,
    });
  });
});
