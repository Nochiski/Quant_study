import { describe, expect, it } from "vitest";

import { strategyRevisionLabel, strategyTitle } from "../model/strategy-titles";

describe("strategy titles", () => {
  const titles = new Map<string, string | null>([
    ["named", "MT-S0 스코프 원본"],
    ["loading", null],
  ]);

  it("shows a placeholder while the name loads and the id only when the lookup failed", () => {
    // 읽는 중에 id 를 보였다가 이름으로 바뀌면 목록에 id 가 먼저 깜빡인다.
    expect(strategyTitle(titles, "loading")).toBe("…");
    expect(strategyTitle(titles, "named")).toBe("MT-S0 스코프 원본");
    expect(strategyTitle(titles, "failed")).toBe("failed");
  });

  it("labels a saved revision with the current name", () => {
    expect(strategyRevisionLabel(titles, "named", 2)).toBe("MT-S0 스코프 원본 · v2");
  });
});
