import { describe, expect, it } from "vitest";

import {
  HEAD_HEIGHT,
  TILE_MIN_HEIGHT,
  TILE_MIN_WIDTH,
  layoutLeaves,
  layoutSectorMap,
  mergeGroups,
  sectorMovers,
  shortWon,
  daysBefore,
  sessionLabel,
  squarify,
  type MapGroup,
  type MapParent,
} from "..";

const group = (
  code: string,
  size: number,
  returnPct: number | null = 1,
): MapGroup => ({
  code,
  name: code,
  sizeWeight: size,
  returnWeight: returnPct === null ? 0 : size,
  returnPct,
});

describe("squarify", () => {
  it("keeps every value's share of the area inside the bounds", () => {
    const values = [500, 300, 120, 50, 20, 10];
    const rects = squarify(values, { x: 0, y: 0, w: 400, h: 200 });
    const total = values.reduce((sum, value) => sum + value, 0);
    rects.forEach((rect, index) => {
      expect(rect.w * rect.h).toBeCloseTo(
        (values[index] / total) * 400 * 200,
        6,
      );
      expect(rect.x).toBeGreaterThanOrEqual(-1e-9);
      expect(rect.y).toBeGreaterThanOrEqual(-1e-9);
      expect(rect.x + rect.w).toBeLessThanOrEqual(400 + 1e-9);
      expect(rect.y + rect.h).toBeLessThanOrEqual(200 + 1e-9);
    });
  });
});

describe("mergeGroups", () => {
  it("weights the change by the return weight and skips groups without a change", () => {
    const merged = mergeGroups(
      [
        { ...group("a", 100, 10), returnWeight: 100 },
        { ...group("b", 300, -10), returnWeight: 100 },
        group("c", 50, null),
      ],
      "x",
      "x",
    );
    expect(merged.sizeWeight).toBe(450);
    // 크기(300)가 아니라 등락 가중(100·100)으로 묶는다
    expect(merged.returnPct).toBeCloseTo(0, 9);
    expect(mergeGroups([group("c", 50, null)], "x", "x").returnPct).toBeNull();
  });
});

describe("layoutSectorMap", () => {
  const parents: MapParent[] = [
    {
      group: group("IT", 2_000),
      children: [
        group("semis", 1_200),
        group("hw", 700),
        group("tiny1", 5),
        group("tiny2", 3),
      ],
    },
    {
      group: group("FIN", 400),
      children: [group("bank", 300), group("ins", 100)],
    },
    { group: group("MAT", 300), children: [group("mat", 300)] },
    { group: group("U1", 4), children: [group("u1", 4)] },
    { group: group("U2", 3), children: [group("u2", 3)] },
  ];
  const layout = layoutSectorMap(parents, 1_000, 320);
  const focusable = layout.tiles.filter((tile) => tile.kind !== "filler");

  it("gives every focusable tile room for a name and a signed change", () => {
    expect(focusable.length).toBeGreaterThan(0);
    for (const tile of focusable) {
      expect(tile.w).toBeGreaterThanOrEqual(TILE_MIN_WIDTH);
      expect(tile.h).toBeGreaterThanOrEqual(TILE_MIN_HEIGHT);
    }
  });

  it("keeps the area of the small sectors it cannot label instead of inflating the rest", () => {
    // 아주 작은 세부 업종 둘·상위 업종 둘은 이름이 들어가지 않아 빈 영역으로 남는다
    const fillers = layout.tiles.filter((tile) => tile.kind === "filler");
    expect(fillers.map((tile) => tile.members).sort()).toEqual([2, 2]);
    expect(layout.tiles.some((tile) => tile.key === "tiny1")).toBe(false);
    const area = layout.tiles.reduce((sum, tile) => sum + tile.w * tile.h, 0);
    const heads = layout.heads.reduce(
      (sum, head) => sum + head.w * HEAD_HEIGHT,
      0,
    );
    expect(area + heads).toBeCloseTo(1_000 * 320, 3);
  });

  it("draws a parent with a single child as one tile without a head", () => {
    const material = layout.tiles.find((tile) => tile.key === "MAT");
    expect(material?.kind).toBe("parent");
    expect(material?.target).toBe("MAT");
    expect(layout.heads.some((head) => head.key === "MAT")).toBe(false);
    expect(layout.heads.some((head) => head.key === "IT")).toBe(true);
  });

  it("drills a leaf into itself", () => {
    expect(layout.tiles.find((tile) => tile.key === "semis")?.target).toBe(
      "semis",
    );
  });
});

describe("layoutLeaves", () => {
  it("keeps as many tiles as fit and labels the grouped rest when it fits", () => {
    const tiles = layoutLeaves(
      [group("a", 900), group("b", 800), group("c", 300), group("d", 250)],
      300,
      300,
      "TABLE",
    );
    const kept = tiles.filter((tile) => tile.kind === "leaf").length;
    const grouped = tiles.find(
      (tile) => tile.kind === "others" || tile.kind === "filler",
    );
    expect(kept + (grouped?.members ?? 0)).toBe(4);
    if (grouped?.kind === "others") expect(grouped.target).toBe("TABLE");
  });
});

describe("sectorMovers", () => {
  it("ranks only sectors above the size floor and splits risers from fallers", () => {
    const { risers, fallers } = sectorMovers(
      [
        group("big-up", 100, 2),
        group("small-up", 1, 9),
        group("mid-up", 50, 1),
        group("flat", 80, 0),
        group("big-down", 100, -5),
        group("mid-down", 60, -1),
        group("none", 100, null),
      ],
      10,
      3,
    );
    expect(risers.map((item) => item.code)).toEqual(["big-up", "mid-up"]);
    expect(fallers.map((item) => item.code)).toEqual(["big-down", "mid-down"]);
  });
});

describe("shortWon", () => {
  it.each([
    [1_454_200_000_000, "1.45조"],
    [17_934_900_000_000, "17.9조"],
    [-463_200_000_000, "4,632억"],
    [30_000_000, "1억 미만"],
    [0, "0억"],
  ])("reads %d as %s", (amount, words) => {
    expect(shortWon(amount)).toBe(words);
  });
});

describe("session dates", () => {
  it("reads a session as month-day with the weekday and counts calendar days", () => {
    expect(sessionLabel("2026-10-30")).toBe("10-30(금)");
    expect(daysBefore("2026-10-30", new Date(2026, 10, 5, 23, 30))).toBe(6);
    expect(daysBefore("2026-11-05", new Date(2026, 10, 5, 0, 1))).toBe(0);
    expect(sessionLabel("bad")).toBe("bad");
  });
});
