import { describe, expect, it } from "vitest";

import type { ChartCandleBody, ChartLineBody } from "../../../entities/chart";
import {
  barWindowOf,
  moveWindowEdge,
  nudgeWindowEdge,
  visibleWindow,
  windowDates,
} from "../model/analysis-window";
import { autoRows } from "../model/auto-rows";
import { finderReducer, INITIAL_PANEL, type FinderPanel } from "../model/finder-panel";
import {
  autoLineTitle,
  changeFromCurrent,
  lineEvidence,
  recreateSeed,
  strengthText,
  zoneBounds,
} from "../model/line-evidence";

const bar = (start: string, end = start): ChartCandleBody => ({
  start,
  end,
  open: 100,
  high: 110,
  low: 90,
  close: 105,
  in_progress: false,
  change_pct: null,
});

// 주봉 여섯: 봉 날짜는 월요일 시작, 끝은 금요일
const WEEKS = ["2025-01-06", "2025-01-13", "2025-01-20", "2025-01-27", "2025-02-03", "2025-02-10"].map((start) => {
  const [y, m, d] = start.split("-").map(Number);
  return bar(start, new Date(Date.UTC(y, m - 1, d + 4)).toISOString().slice(0, 10));
});

describe("analysis window edges", () => {
  it("snaps an edge to whole bars and keeps it inside the completed bars", () => {
    // 봉 2.6 → 3, 끝은 계열 끝(5)을 넘지 못한다
    expect(moveWindowEdge({ start: 1, end: 4 }, "start", 2.6, 6)).toEqual({ start: 3, end: 4 });
    expect(moveWindowEdge({ start: 1, end: 4 }, "end", 9, 6)).toEqual({ start: 1, end: 5 });
    expect(moveWindowEdge({ start: 1, end: 4 }, "start", -3, 6)).toEqual({ start: 0, end: 4 });
  });

  it("never lets one edge cross the other — the window keeps two bars", () => {
    // 시작을 끝(4) 위로 끌어도 3에서 멈춘다(시작 < 끝)
    expect(moveWindowEdge({ start: 1, end: 4 }, "start", 5, 6)).toEqual({ start: 3, end: 4 });
    expect(moveWindowEdge({ start: 1, end: 4 }, "end", 0, 6)).toEqual({ start: 1, end: 2 });
    // 봉이 하나뿐이면 구간을 바꾸지 않는다
    expect(moveWindowEdge({ start: 0, end: 0 }, "end", 3, 1)).toEqual({ start: 0, end: 0 });
  });

  it("moves an edge one bar per arrow key, ten with Shift, and to its limit with Home/End", () => {
    expect(nudgeWindowEdge({ start: 1, end: 4 }, "end", "ArrowLeft", false, 6)).toEqual({ start: 1, end: 3 });
    expect(nudgeWindowEdge({ start: 12, end: 40 }, "start", "ArrowRight", true, 50)).toEqual({ start: 22, end: 40 });
    expect(nudgeWindowEdge({ start: 1, end: 4 }, "start", "Home", false, 6)).toEqual({ start: 0, end: 4 });
    expect(nudgeWindowEdge({ start: 1, end: 4 }, "end", "End", false, 6)).toEqual({ start: 1, end: 5 });
    // 끝 테두리의 Home은 시작 바로 뒤 봉까지만(구간은 두 봉 이상)
    expect(nudgeWindowEdge({ start: 1, end: 4 }, "end", "Home", false, 6)).toEqual({ start: 1, end: 2 });
    expect(nudgeWindowEdge({ start: 1, end: 4 }, "start", "Enter", false, 6)).toBeNull();
  });

  it("maps response dates to bars and back to the bar start dates sent in requests", () => {
    expect(barWindowOf(WEEKS, { start: "2025-01-13", end: "2025-02-03" })).toEqual({ start: 1, end: 4 });
    // 직접 넣은 주 중간 날짜(수요일)·주말은 그 날짜가 든 주봉이다(서버 `index_of`처럼) — 음영이 사라지지 않는다
    expect(barWindowOf(WEEKS, { start: "2025-01-15", end: "2025-02-08" })).toEqual({ start: 1, end: 4 });
    // 첫 봉 앞이거나 마지막 봉의 끝(2025-02-14 금) 뒤면 봉이 없다
    expect(barWindowOf(WEEKS, { start: "2025-01-01", end: "2025-02-03" })).toBeNull();
    expect(barWindowOf(WEEKS, { start: "2025-01-13", end: "2025-02-15" })).toBeNull();
    expect(windowDates(WEEKS, { start: 1, end: 4 })).toEqual({ start: "2025-01-13", end: "2025-02-03" });
    // 화면 기간: 보이는 완성 봉의 첫·마지막 봉 시작일
    expect(visibleWindow(WEEKS.slice(2, 5))).toEqual({ start: "2025-01-20", end: "2025-02-03" });
    expect(visibleWindow(WEEKS.slice(2, 3))).toBeNull();
  });
});

describe("finder panel", () => {
  const opened = finderReducer(INITIAL_PANEL, {
    type: "openCreate",
    tool: "fib_channel",
    seed: { window: { start: "2025-01-06", end: "2025-02-03" }, firstAnchor: "2025-01-13" },
    returnKey: "new:fib_channel",
  });
  const draft = (panel: FinderPanel) => (panel.view === "create" ? panel.draft : null);

  it("returns to the layers with the opener's key so focus can go back to it", () => {
    expect(finderReducer(opened, { type: "close" })).toEqual({ view: "layers", focusKey: "new:fib_channel" });
    // 계열이 바뀌면 닫되 초점을 옮기지 않는다
    expect(finderReducer(opened, { type: "reset" })).toEqual(INITIAL_PANEL);
  });

  it("starts again from the first candidate whenever an input changes", () => {
    const picked = finderReducer(opened, { type: "pickCandidate", index: 2 });
    expect(draft(picked)?.candidate).toBe(2);
    const sensitivity = finderReducer(picked, { type: "edit", input: { candidateSensitivity: 1.5 } });
    expect(draft(sensitivity)).toMatchObject({ candidate: 0, candidateSensitivity: 1.5, firstAnchor: "2025-01-13" });
  });

  it("opens with the first-point mode when the seed has a first point, and counts only hand edits as changed", () => {
    expect(draft(opened)).toMatchObject({ anchorMode: "first", paramsTouched: false });
    // 다시 만들기로 채운 저장 민감도는 바꾼 값이 아니다
    const seeded = finderReducer(INITIAL_PANEL, {
      type: "openCreate",
      tool: "fib_channel",
      seed: { candidateSensitivity: 1, structureSensitivity: 2 },
      returnKey: "k",
    });
    expect(draft(seeded)).toMatchObject({ anchorMode: "auto", paramsTouched: false });
    expect(draft(finderReducer(seeded, { type: "edit", input: { candidateSensitivity: 1.5 } }))?.paramsTouched).toBe(true);
    // 세 점 자동으로 돌아가면 첫 점을 비운다
    expect(draft(finderReducer(opened, { type: "edit", input: { anchorMode: "auto" } }))).toMatchObject({
      anchorMode: "auto",
      firstAnchor: null,
    });
  });

  it("forgets the first point when the window or direction changes — it was picked from that window", () => {
    const moved = finderReducer(opened, { type: "edit", input: { window: { start: "2025-01-13", end: "2025-02-10" } } });
    expect(draft(moved)?.firstAnchor).toBeNull();
    const turned = finderReducer(opened, { type: "edit", input: { direction: "down" } });
    expect(draft(turned)?.firstAnchor).toBeNull();
  });

  it("toggles found zones out of the add list and back", () => {
    const zones = finderReducer(INITIAL_PANEL, { type: "openCreate", tool: "hline", returnKey: "new:hline" });
    const off = finderReducer(finderReducer(zones, { type: "toggleZone", index: 1 }), { type: "toggleZone", index: 3 });
    expect(draft(off)?.excludedZones).toEqual([1, 3]);
    expect(draft(finderReducer(off, { type: "toggleZone", index: 1 }))?.excludedZones).toEqual([3]);
  });
});

const line = (patch: Partial<ChartLineBody>): ChartLineBody => ({
  line_id: "l1",
  instrument_kind: "index",
  instrument_code: "practice:KOSPI:코스피",
  timeframe: "week",
  kind: "fib_channel",
  source: "auto",
  anchors: [],
  levels: null,
  extend_right: true,
  memo: "",
  locked: false,
  visible: true,
  auto: null,
  created_at: "2025-10-31T00:00:00Z",
  updated_at: "2025-10-31T00:00:00Z",
  paths: [],
  issue: null,
  created: null,
  ...patch,
});

const CHANNEL_RECORD = {
  algorithm_version: "chart_analysis@1",
  params: {
    candidate_sensitivity: 1,
    structure_sensitivity: 2,
    direction: "down" as const,
    first_anchor: "2025-02-24",
  },
  regime_window_start: "2024-10-28",
  regime_window_end: "2025-10-20",
  regime: { verdict: "hold" as const, strength: -1.75, structure: "down" as const },
  chosen_by: null,
  window_source: "preset" as const,
};

describe("line evidence", () => {
  const channel = line({
    anchors: [
      { bar_date: "2025-02-24", price: 2349.37 },
      { bar_date: "2025-09-15", price: 2106.64 },
      { bar_date: "2025-04-07", price: 1968.54 },
    ],
    paths: [
      { level: 0, start: { bar_date: "2025-02-24", price: 2349.37 }, end: { bar_date: "2025-10-20", price: 2100 } },
      { level: 1, start: { bar_date: "2025-02-24", price: 2018.76 }, end: { bar_date: "2025-10-20", price: 1800 } },
    ],
    auto: CHANNEL_RECORD,
    created: "2025-09-29",
  });

  it("names a falling channel's anchors and reads this bar's level prices against the current price", () => {
    const evidence = lineEvidence([channel], 2000);
    expect(evidence.direction).toBe("down");
    expect(evidence.anchors.map((anchor) => anchor.role)).toEqual([
      "chart.evidence.role.channel.down.0",
      "chart.evidence.role.channel.down.1",
      "chart.evidence.role.channel.down.2",
    ]);
    // 2100 ÷ 2000 − 1 = +5%, 1800 ÷ 2000 − 1 = −10% (손계산)
    expect(evidence.levels.map((level) => [level.level, level.price, level.barDate])).toEqual([
      [0, 2100, "2025-10-20"],
      [1, 1800, "2025-10-20"],
    ]);
    expect(evidence.levels[0].changePct).toBeCloseTo(5);
    expect(evidence.levels[1].changePct).toBeCloseTo(-10);
    expect(evidence.created).toBe("2025-09-29");
    // 값이 null인 기록 칸은 알고리즘 줄에서 뺀다
    expect(evidence.params.map((param) => param.key)).toEqual([
      "candidate_sensitivity",
      "structure_sensitivity",
      "direction",
      "first_anchor",
    ]);
  });

  it("shows a zone group as bands with their formation dates instead of anchors", () => {
    const zones = [
      line({ line_id: "z1", kind: "hline", anchors: [{ bar_date: "2025-01-06", price: 2017 }, { bar_date: "2025-01-06", price: 1990 }], created: "2025-03-03" }),
      line({ line_id: "z2", kind: "hline", anchors: [{ bar_date: "2025-02-03", price: 2500 }, { bar_date: "2025-02-03", price: 2550 }], created: null }),
    ];
    const evidence = lineEvidence(zones, null);
    expect(evidence.anchors).toEqual([]);
    expect(evidence.zones).toEqual([
      { lineId: "z1", low: 1990, high: 2017, formed: "2025-03-03" },
      { lineId: "z2", low: 2500, high: 2550, formed: null },
    ]);
  });

  it("refills the panel from the stored record to make the line again", () => {
    expect(recreateSeed(channel)).toMatchObject({
      window: { start: "2024-10-28", end: "2025-10-20" },
      candidateSensitivity: 1,
      structureSensitivity: 2,
      direction: "down" as const,
      firstAnchor: "2025-02-24",
      levels: null,
      extendRight: true,
    });
    // 기록이 없거나 구간 한쪽이 비면 서버 기본 구간으로 연다
    expect(recreateSeed(line({ auto: null }))).toMatchObject({ window: null, direction: "recommended" });
  });

  it("reads no change when the current price is missing", () => {
    expect(changeFromCurrent(110, null)).toBeNull();
    expect(changeFromCurrent(110, 100)).toBeCloseTo(10);
  });

  it("names a row and formats its numbers one way everywhere", () => {
    expect(autoLineTitle([channel], "week")).toBe("하락 채널");
    expect(autoLineTitle([channel], "day")).toBe("하락 채널 · 주봉");
    expect(autoLineTitle([line({ kind: "hline" }), line({ kind: "hline" })], "week")).toBe("수평 지지·저항 구간 · 2곳");
    expect(strengthText(-1.754)).toBe("−1.75");
    expect(strengthText(4.11)).toBe("+4.11");
    expect(strengthText(null)).toBe("—");
    expect(zoneBounds(line({ anchors: [{ bar_date: "2025-01-06", price: 2017 }, { bar_date: "2025-01-06", price: 1990 }] }))).toEqual({
      low: 1990,
      high: 2017,
    });
  });
});

describe("auto rows", () => {
  it("lists auto lines one row each but bundles zones made together, and leaves my lines out", () => {
    const record = { ...CHANNEL_RECORD, params: { candidate_sensitivity: 1, structure_sensitivity: 2, width_atr: 0.5, min_touches: 2 } };
    const rows = autoRows([
      line({ line_id: "c1" }),
      line({ line_id: "z1", kind: "hline", auto: record }),
      line({ line_id: "m1", source: "manual" }),
      line({ line_id: "z2", kind: "hline", auto: record }),
      line({ line_id: "z3", kind: "hline", auto: { ...record, params: { ...record.params, width_atr: 1 } } }),
      line({ line_id: "copy", source: "auto_copied" }),
    ]);
    expect(rows.map((row) => row.lines.map((item) => item.line_id))).toEqual([["c1"], ["z1", "z2"], ["z3"]]);
  });
});
