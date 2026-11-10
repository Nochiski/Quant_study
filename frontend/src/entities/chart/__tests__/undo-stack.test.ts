import { describe, expect, it } from "vitest";

import { UNDO_LIMIT, popUndo, pushUndo, recreateChartLine, type ChartLineBody } from "..";

const line = (id: string, values: Partial<ChartLineBody> = {}): ChartLineBody => ({
  line_id: id,
  instrument_kind: "stock",
  instrument_code: "practice:005930",
  timeframe: "week",
  kind: "hline",
  source: "manual",
  anchors: [{ bar_date: "2025-03-03", price: 12_345 }],
  levels: null,
  extend_right: false,
  memo: "지지",
  locked: false,
  visible: false,
  auto: null,
  created: null,
  created_at: "2026-11-06T00:00:00Z",
  updated_at: "2026-11-06T00:00:00Z",
  issue: null,
  paths: [],
  ...values,
});

describe("undo stack", () => {
  it("recreates the last deleted line first with the same content and no id", () => {
    const first = line("a", { memo: "첫째" });
    const second = line("b", { kind: "fib_retracement", levels: [0, 0.5, 1] });
    const stack = pushUndo(pushUndo([], [first], "a"), [second], "b");
    const [top, rest] = popUndo(stack);
    expect(top).toEqual({ title: "b", bodies: [recreateChartLine(second)] });
    expect(top?.bodies[0]).not.toHaveProperty("line_id");
    expect(top?.bodies[0].levels).toEqual([0, 0.5, 1]);
    expect(popUndo(rest)[0]?.bodies[0].memo).toBe("첫째");
    expect(popUndo([])).toEqual([null, []]);
  });

  it("keeps lines deleted together in one slot so one undo restores them all", () => {
    const zones = [line("z1", { memo: "아래" }), line("z2", { memo: "위" })];
    const [top, rest] = popUndo(pushUndo(pushUndo([], [line("a")], "a"), zones, "구간"));
    expect(top?.bodies.map((body) => body.memo)).toEqual(["아래", "위"]);
    expect(rest).toHaveLength(1);
  });

  it("keeps only the most recent deletions", () => {
    let stack: ReturnType<typeof pushUndo> = [];
    for (let index = 0; index < UNDO_LIMIT + 3; index += 1)
      stack = pushUndo(stack, [line(`l${index}`, { memo: String(index) })], `l${index}`);
    expect(stack).toHaveLength(UNDO_LIMIT);
    expect(stack[0].bodies[0].memo).toBe("3");
  });
});
