import { describe, expect, it } from "vitest";

import { chartLineEdit, type ChartLineBody } from "../../../entities/chart";
import { ANCHOR_NAMES } from "../model/anchor-draft";
import {
  CLICKS_PER_LINE,
  STEP_HINTS,
  completedAnchors,
  drawingReducer,
  INITIAL_DRAWING,
} from "../model/drawing";
import { UNDO_LIMIT, nudgedAnchors, popUndo, pushUndo, recreateChartLine } from "../model/line-edit";

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

describe("chartLineEdit", () => {
  it("carries every editable field because PUT resets omitted ones to defaults", () => {
    // 서버 기본값은 연장 켜짐·보이기 켜짐·메모 빈칸이다 — 빠뜨리면 숨긴 선이 다시 보이고 메모가 지워진다
    expect(chartLineEdit(line("a"), { locked: true })).toEqual({
      anchors: [{ bar_date: "2025-03-03", price: 12_345 }],
      extend_right: false,
      memo: "지지",
      locked: true,
      visible: false,
      levels: null,
    });
  });
});

describe("undo stack", () => {
  it("recreates the last deleted line first with the same content and no id", () => {
    const first = line("a", { memo: "첫째" });
    const second = line("b", { kind: "fib_retracement", levels: [0, 0.5, 1] });
    const stack = pushUndo(pushUndo([], first), second);
    const [top, rest] = popUndo(stack);
    expect(top).toEqual(recreateChartLine(second));
    expect(top).not.toHaveProperty("line_id");
    expect(top?.levels).toEqual([0, 0.5, 1]);
    expect(popUndo(rest)[0]?.memo).toBe("첫째");
    expect(popUndo([])).toEqual([null, []]);
  });

  it("keeps only the most recent deletions", () => {
    let stack: ReturnType<typeof pushUndo> = [];
    for (let index = 0; index < UNDO_LIMIT + 3; index += 1)
      stack = pushUndo(stack, line(`l${index}`, { memo: String(index) }));
    expect(stack).toHaveLength(UNDO_LIMIT);
    expect(stack[0].memo).toBe("3");
  });
});

describe("nudgedAnchors", () => {
  const stock = {
    kind: "stock" as const,
    tick_steps: [
      { below: 20_000, tick: 10 },
      { below: null, tick: 50 },
    ],
  };

  it("moves a horizontal line by one tick or by 1% with shift", () => {
    expect(nudgedAnchors(line("a"), stock, 1, false)).toEqual([{ bar_date: "2025-03-03", price: 12_350 }]);
    // 12,345 × 0.99 = 12,221.55 → 호가 10원 아래로 12,220
    expect(nudgedAnchors(line("a"), stock, -1, true)).toEqual([{ bar_date: "2025-03-03", price: 12_220 }]);
  });

  it("does not move locked, auto or multi-anchor lines", () => {
    expect(nudgedAnchors(line("a", { locked: true }), stock, 1, false)).toBeNull();
    expect(nudgedAnchors(line("a", { source: "auto" }), stock, 1, false)).toBeNull();
    expect(nudgedAnchors(line("a", { kind: "fib_channel" }), stock, 1, false)).toBeNull();
  });
});

describe("anchor names and step hints", () => {
  it("name one row and one hint per click of each tool", () => {
    for (const kind of Object.keys(CLICKS_PER_LINE) as (keyof typeof CLICKS_PER_LINE)[]) {
      expect(ANCHOR_NAMES[kind], kind).toHaveLength(CLICKS_PER_LINE[kind]);
      expect(STEP_HINTS[kind], kind).toHaveLength(CLICKS_PER_LINE[kind]);
    }
  });
});

describe("drawing clicks", () => {
  const point = (price: number) => ({ bar_date: "2025-03-03", price });

  it("finishes a retracement on the second click and a channel on the third", () => {
    const retracement = drawingReducer(INITIAL_DRAWING, { type: "pickTool", tool: "fib_retracement" });
    expect(completedAnchors(retracement, point(1))).toBeNull();
    const placed = drawingReducer(retracement, { type: "addPoint", point: point(1) });
    expect(completedAnchors(placed, point(2))).toEqual([point(1), point(2)]);

    const channel = drawingReducer(INITIAL_DRAWING, { type: "pickTool", tool: "fib_channel" });
    const two = [point(1), point(2)].reduce(
      (state, item) => drawingReducer(state, { type: "addPoint", point: item }),
      channel,
    );
    expect(completedAnchors(two, point(3))).toHaveLength(3);
    expect(completedAnchors(INITIAL_DRAWING, point(1))).toBeNull();
  });

  it("drops placed points when the tool changes and returns to select after finishing", () => {
    const placed = drawingReducer(
      drawingReducer(INITIAL_DRAWING, { type: "pickTool", tool: "fib_channel" }),
      { type: "addPoint", point: point(1) },
    );
    expect(drawingReducer(placed, { type: "pickTool", tool: "hline" }).points).toEqual([]);
    expect(drawingReducer(placed, { type: "finished", lineId: "new" })).toMatchObject({
      tool: "select",
      points: [],
      selectedId: "new",
    });
  });
});
