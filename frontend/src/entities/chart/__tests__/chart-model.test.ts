import { describe, expect, it } from "vitest";

import {
  chartScale,
  displayRange,
  fractionalIndex,
  levelSegments,
  monthsBefore,
  priceTicks,
  snapToCandle,
  spreadLabels,
  steppedPrice,
  stockChartSearch,
  visibleRange,
  type ChartCandleBody,
} from "..";

const candle = (start: string, end: string, values: Partial<ChartCandleBody> = {}): ChartCandleBody => ({
  start,
  end,
  open: 100,
  high: 110,
  low: 90,
  close: 105,
  in_progress: false,
  ...values,
});

// 주봉 넷: 월요일 시작, 둘째 주는 월요일 휴장이라 화요일 시작
const WEEKS = [
  candle("2025-01-06", "2025-01-10"),
  candle("2025-01-14", "2025-01-17"),
  candle("2025-01-20", "2025-01-24"),
  candle("2025-01-27", "2025-01-30", { in_progress: true }),
];

describe("chartScale", () => {
  // 폭 484 = 그림 칸 400 + 가격 축 84, 봉 4 + 미래 1 = 5칸 → 칸 폭 80
  const scale = chartScale({ candles: WEEKS, first: 0, last: 3, width: 484, height: 228, futureBars: 1 });

  it("puts bar centres at half steps and reads the nearest bar back from x", () => {
    expect(scale.step).toBe(80);
    expect(scale.xOfIndex(0)).toBe(40);
    expect(scale.xOfIndex(3)).toBe(280);
    expect(scale.futureLeft).toBe(320);
    expect(scale.indexAtX(119)).toBe(1);
    expect(scale.indexAtX(121)).toBe(1);
    expect(scale.indexAtX(161)).toBe(2);
  });

  it("maps prices linearly with 6% padding and inverts y back to the price", () => {
    // 저가 90 ~ 고가 110, 여유 1.2씩 → 88.8 ~ 111.2, 그림 칸 y 12 ~ 200
    expect(scale.priceMin).toBeCloseTo(88.8);
    expect(scale.priceMax).toBeCloseTo(111.2);
    expect(scale.yOfPrice(100)).toBeCloseTo(106);
    expect(scale.priceAtY(106)).toBeCloseTo(100);
  });

  it("places a date inside a bar by calendar days and a bar start exactly on the bar", () => {
    expect(scale.xOfDate("2025-01-14")).toBe(120);
    // 1-08은 1-06 봉 안 2일째 / 그 봉 길이 8일(1-06 → 1-14) = 0.25봉
    expect(fractionalIndex(WEEKS, "2025-01-08")).toBe(0.25);
    expect(scale.xOfDate("2025-01-08")).toBe(60);
    // 휴장 첫날(1-13 월)은 앞 봉의 끝자락이다
    expect(fractionalIndex(WEEKS, "2025-01-13")).toBe(0.875);
  });
});

describe("priceTicks", () => {
  it("steps by 1-2-5 units inside the range", () => {
    // 22.4 / 4 = 5.6 → 단위 10
    expect(priceTicks(88.8, 111.2, 4)).toEqual([90, 100, 110]);
    expect(priceTicks(2_284.7, 2_760.1, 5)).toEqual([2_300, 2_400, 2_500, 2_600, 2_700]);
    expect(priceTicks(0.1, 0.35, 5)).toEqual([0.1, 0.15, 0.2, 0.25, 0.3, 0.35]);
  });
});

describe("levelSegments", () => {
  const scale = chartScale({ candles: WEEKS, first: 0, last: 3, width: 484, height: 228, futureBars: 1 });
  const path = {
    level: 0.5,
    start: { bar_date: "2025-01-06", price: 100 },
    end: { bar_date: "2025-01-20", price: 105 },
  };

  it("joins the two server points and extends with the same slope to the plot edge", () => {
    const [segment] = levelSegments([path], true, scale);
    // (40, y100) → (200, y105), 기울기 그대로 x 400까지
    const y100 = scale.yOfPrice(100);
    const y105 = scale.yOfPrice(105);
    expect(segment.x1).toBe(40);
    expect(segment.x2).toBe(400);
    expect(segment.y2).toBeCloseTo(y105 + ((y105 - y100) / 160) * 200);
    expect(segment.endY).toBeCloseTo(y105);
    expect(segment.endPrice).toBe(105);
  });

  it("stops at the last completed bar when not extended", () => {
    const [segment] = levelSegments([path], false, scale);
    expect(segment.x2).toBe(200);
    expect(segment.y2).toBeCloseTo(scale.yOfPrice(105));
  });
});

describe("snapToCandle", () => {
  const bar = candle("2025-01-06", "2025-01-10", { open: 100, high: 112, low: 95, close: 104 });

  it("picks the open/high/low/close value nearest to the pointer price", () => {
    expect(snapToCandle(bar, 111)).toEqual({ field: "high", price: 112 });
    expect(snapToCandle(bar, 96.9)).toEqual({ field: "low", price: 95 });
    expect(snapToCandle(bar, 103)).toEqual({ field: "close", price: 104 });
    // 시가 100과 종가 104의 한가운데 102는 앞의 시가
    expect(snapToCandle(bar, 102)).toEqual({ field: "open", price: 100 });
  });
});

describe("spreadLabels", () => {
  it("pushes overlapping badges apart by the gap and keeps input order", () => {
    // 100·105·108은 20 간격으로 100·120·140, 300은 그대로
    expect(spreadLabels([105, 300, 100, 108], 20, 0, 400)).toEqual([120, 300, 100, 140]);
  });

  it("lifts the stack back up when it would run past the bottom", () => {
    // 아래 끝 200: 190·195 → 200에서 거꾸로 180·200
    expect(spreadLabels([190, 195], 20, 0, 200)).toEqual([180, 200]);
  });
});

describe("steppedPrice", () => {
  const stock = { kind: "stock" as const, group: "common" };

  it("moves one tick along the KRX tick grid", () => {
    expect(steppedPrice(stock, 71_500, 1)).toBe(71_600);
    expect(steppedPrice(stock, 71_500, -1)).toBe(71_400);
    // 눈금 밖의 값은 다음 눈금으로 맞춘다(호가 10원 구간)
    expect(steppedPrice(stock, 12_343, 1)).toBe(12_350);
    expect(steppedPrice(stock, 12_343, -1)).toBe(12_340);
    // 구간 경계: 2,000 아래는 1원 호가
    expect(steppedPrice(stock, 2_000, -1)).toBe(1_999);
    expect(steppedPrice(stock, 1_999, 1)).toBe(2_000);
  });

  it("moves an index by 0.01 and by percent with display rounding", () => {
    const index = { kind: "index" as const, group: "KOSPI" };
    expect(steppedPrice(index, 2_563.12, 1)).toBe(2_563.13);
    expect(steppedPrice(index, 2_500, 1, 1)).toBe(2_525);
    expect(steppedPrice(stock, 71_500, -1, 1)).toBe(70_785);
  });
});

describe("stockChartSearch", () => {
  it("keeps a valid instrument, timeframe and custom range", () => {
    expect(
      stockChartSearch({
        kind: "index",
        code: "KOSPI:코스피",
        tf: "day",
        start: "2024-01-02",
        end: "2024-06-28",
        period: "1y",
      }),
    ).toEqual({
      kind: "index",
      code: "KOSPI:코스피",
      tf: "day",
      period: undefined,
      start: "2024-01-02",
      end: "2024-06-28",
    });
  });

  it("drops unknown values, half instruments, impossible dates and reversed ranges", () => {
    expect(stockChartSearch({ code: "005930", tf: "hour", period: "3y" })).toEqual({
      tf: undefined,
      period: undefined,
      start: undefined,
      end: undefined,
    });
    expect(stockChartSearch({ start: "2024-02-30", end: "2024-03-01", period: "6m" }).start).toBeUndefined();
    expect(stockChartSearch({ start: "2024-05-01", end: "2024-03-01", period: "6m" })).toMatchObject({
      period: "6m",
      start: undefined,
    });
  });
});

describe("displayRange and visibleRange", () => {
  it("counts preset periods back from the ledger date with month-end clamping", () => {
    expect(monthsBefore("2025-03-31", 1)).toBe("2025-02-28");
    expect(monthsBefore("2025-10-30", 24)).toBe("2023-10-30");
    expect(displayRange({ period: "6m" }, "2025-10-30")).toEqual({ start: "2025-04-30", end: "2025-10-30" });
    expect(displayRange({}, "2025-10-30").start).toBe("2023-10-30");
  });

  it("shows every bar whose period touches the range", () => {
    // 1-08~1-21: 첫 주는 끝(1-10)이 걸치고, 셋째 주는 시작(1-20)이 걸친다
    expect(visibleRange(WEEKS, { start: "2025-01-08", end: "2025-01-21" })).toEqual({ first: 0, last: 2 });
    expect(visibleRange(WEEKS, { start: "2025-02-01", end: "2025-03-01" })).toBeNull();
  });
});
