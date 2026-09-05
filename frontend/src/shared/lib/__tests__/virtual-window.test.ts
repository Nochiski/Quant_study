import { describe, expect, it } from "vitest";

import { projectVirtualWindow } from "../virtual-window";

describe("virtual window projection", () => {
  it("renders small collections without virtualization", () => {
    expect(
      projectVirtualWindow({
        itemCount: 20,
        itemHeight: 40,
        scrollTop: 400,
        viewportHeight: 200,
      }),
    ).toEqual({
      start: 0,
      end: 20,
      paddingBefore: 0,
      paddingAfter: 0,
      virtualized: false,
    });
  });

  it("bounds a large collection and preserves its total scroll geometry", () => {
    const range = projectVirtualWindow({
      itemCount: 8_000,
      itemHeight: 40,
      scrollTop: 40_000,
      viewportHeight: 400,
      overscan: 5,
    });

    expect(range).toEqual({
      start: 995,
      end: 1_015,
      paddingBefore: 39_800,
      paddingAfter: 279_400,
      virtualized: true,
    });
    expect(
      range.paddingBefore + (range.end - range.start) * 40 + range.paddingAfter,
    ).toBe(320_000);
  });

  it("clamps hostile metrics to the final bounded window", () => {
    const range = projectVirtualWindow({
      itemCount: 100,
      itemHeight: 50,
      scrollTop: Number.POSITIVE_INFINITY,
      viewportHeight: 250,
      overscan: 2,
    });

    expect(range.end).toBe(100);
    expect(range.start).toBe(93);
    expect(range.end - range.start).toBeLessThan(20);
  });
});
