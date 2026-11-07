import { describe, expect, it } from "vitest";

import {
  chartScale,
  dateTicks,
  defaultViewport,
  displayRange,
  formedSegments,
  fractionalIndex,
  labelAnchor,
  levelSegments,
  monthsBefore,
  panViewport,
  placeLevelLabels,
  priceTicks,
  shadedBands,
  snapToCandle,
  spreadLabels,
  steppedPrice,
  stockChartSearch,
  switchSeries,
  viewportRange,
  visibleBars,
  visibleRange,
  wheelZoomFactor,
  widensRange,
  zoomViewport,
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
  change_pct: null,
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
  const scale = chartScale({ candles: WEEKS, view: { from: -0.5, to: 4.5 }, width: 484, height: 228 });

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

describe("chartScale with a zoomed view", () => {
  // 첫 봉 고가만 200 — 보기 창 밖이면 가격 축에 들지 않는다
  const candles = [candle("2025-01-06", "2025-01-10", { high: 200 }), ...WEEKS.slice(1)];

  it("draws only the bars the view touches and fits the price axis to them", () => {
    // 봉 1~3 + 미래 3칸 = 6칸 → 칸 폭 400 / 6
    const scale = chartScale({ candles, view: { from: 0.5, to: 6.5 }, width: 484, height: 228 });
    expect([scale.first, scale.last]).toEqual([1, 3]);
    expect(scale.step).toBeCloseTo(400 / 6);
    expect(scale.xOfIndex(1)).toBeCloseTo(400 / 12);
    expect(scale.indexAtX(400 / 12)).toBe(1);
    expect(scale.futureLeft).toBeCloseTo(200);
    expect(scale.priceMax).toBeCloseTo(111.2);
    // 첫 봉까지 보이면 그 고가가 가격 축 위 끝을 정한다(90 ~ 200, 여유 6.6)
    expect(chartScale({ candles, view: { from: -0.5, to: 4.5 }, width: 484, height: 228 }).priceMax).toBeCloseTo(206.6);
  });
});

describe("chart viewport", () => {
  it("counts every bar the view touches, clipped to the series", () => {
    expect(visibleBars({ from: -0.5, to: 4.5 }, 4)).toEqual({ first: 0, last: 3 });
    // 2.7 ~ 10.2: 봉 3([2.5, 3.5])부터 봉 10([9.5, 10.5])까지 걸친다
    expect(visibleBars({ from: 2.7, to: 10.2 }, 20)).toEqual({ first: 3, last: 10 });
  });

  it("right-aligns the latest bar with a future margin and leaves past ranges unpadded", () => {
    // 1-14~1-30: 봉 1~3, 마지막이 최신 봉이라 미래 3칸(적어도 3봉)
    expect(defaultViewport(WEEKS, { start: "2025-01-14", end: "2025-01-30" })).toEqual({ from: 0.5, to: 6.5 });
    // 1-06~1-15: 봉 0~1, 과거 구간이라 여백 없음 — 두 봉뿐이라 오른쪽 끝(1.5)을 둔 채 최소 폭(계열 4봉)으로 넓힌다
    expect(defaultViewport(WEEKS, { start: "2025-01-06", end: "2025-01-15" })).toEqual({ from: -2.5, to: 1.5 });
    expect(defaultViewport(WEEKS, { start: "2025-02-01", end: "2025-03-01" })).toBeNull();
  });

  it("keeps a one-bar series right-aligned instead of pushing it a slot left", () => {
    // 봉 하나: 미래 여백 3봉이면 빈칸(3)이 보기(4칸)의 절반을 넘어 한도에 밀린다 — 여백을 1봉으로 줄여 봉이 왼쪽
    // 끝 [-0.5, 0.5]에 그대로 붙고 미래 1칸이 남는다
    const one = [candle("2025-01-06", "2025-01-10")];
    expect(defaultViewport(one, { start: "2025-01-06", end: "2025-01-10" })).toEqual({ from: -0.5, to: 1.5 });
    // 봉 둘: 여백 2봉 = 보기 4칸의 절반
    expect(defaultViewport(WEEKS.slice(0, 2), { start: "2025-01-06", end: "2025-01-15" })).toEqual({
      from: -0.5,
      to: 3.5,
    });
  });

  it("tells when a short date range is widened to the minimum span", () => {
    const daily = Array.from({ length: 100 }, (_, index) => {
      const day = new Date(Date.UTC(2024, 0, 1 + index)).toISOString().slice(0, 10);
      return candle(day, day);
    });
    expect(widensRange(daily, { start: daily[40].start, end: daily[41].start })).toBe(true);
    expect(widensRange(daily, { start: daily[40].start, end: daily[49].start })).toBe(false);
    expect(widensRange(daily, { start: "2030-01-01", end: "2030-02-01" })).toBe(false);
  });

  it("zooms around the cursor so the bar under it stays put", () => {
    // 40 ~ 80(40칸), 커서 1/4 자리(봉 50) — 반으로 확대하면 20칸이고 봉 50은 여전히 1/4 자리
    expect(zoomViewport({ from: 40, to: 80 }, 0.5, 0.25, 100)).toEqual({ from: 45, to: 65 });
  });

  it("widens a few-bar range to the minimum span", () => {
    // 봉 100개 중 봉 40~41(이틀)만 걸친 기간 → 오른쪽 끝 41.5를 둔 채 10칸
    const daily = Array.from({ length: 100 }, (_, index) => {
      const day = new Date(Date.UTC(2024, 0, 1 + index)).toISOString().slice(0, 10);
      return candle(day, day);
    });
    expect(defaultViewport(daily, { start: daily[40].start, end: daily[41].start })).toEqual({ from: 31.5, to: 41.5 });
  });

  it("never lets the widest zoom be narrower than the default view on a short series", () => {
    // 봉 8개: 1.25배(10칸)보다 기본 보기(8봉 + 미래 3칸 = 11칸)가 넓다 — 축소 휠이 확대로 뒤집히지 않는다
    expect(zoomViewport({ from: -0.5, to: 10.5 }, 1.5, 0.5, 8)).toEqual({ from: -0.5, to: 10.5 });
  });

  it("limits the zoom between ten bars and 1.25 times the series", () => {
    // 오른쪽 끝을 붙들고 최소 10칸
    expect(zoomViewport({ from: 40, to: 80 }, 0.01, 1, 100)).toEqual({ from: 70, to: 80 });
    // 봉 100개 → 최대 125칸, 가운데(봉 60)를 붙든다
    expect(zoomViewport({ from: 40, to: 80 }, 10, 0.5, 100)).toEqual({ from: -2.5, to: 122.5 });
  });

  it("pans but never leaves more than half the view empty", () => {
    expect(panViewport({ from: 40, to: 80 }, 10, 100)).toEqual({ from: 50, to: 90 });
    // 오른쪽 끝 한계 = 마지막 봉 끝 99.5 + 40칸의 절반
    expect(panViewport({ from: 40, to: 80 }, 100, 100)).toEqual({ from: 79.5, to: 119.5 });
    // 왼쪽 끝 한계 = 첫 봉 앞 -0.5 - 20
    expect(panViewport({ from: 40, to: 80 }, -200, 100)).toEqual({ from: -20.5, to: 19.5 });
  });

  it("reads the visible date range back as the same bars", () => {
    const range = viewportRange({ from: 0.5, to: 6.5 }, WEEKS);
    expect(range).toEqual({ start: "2025-01-14", end: "2025-01-30" });
    expect(visibleRange(WEEKS, range!)).toEqual({ first: 1, last: 3 });
  });

  it("turns one mouse wheel notch into about 16% and caps a burst at twice", () => {
    expect(wheelZoomFactor(100)).toBeCloseTo(Math.exp(0.15));
    expect(wheelZoomFactor(-100)).toBeCloseTo(Math.exp(-0.15));
    expect(wheelZoomFactor(10_000)).toBeLessThan(2);
  });
});

describe("auto line drawing", () => {
  const segment = (level: number | null, y1: number, y2: number) => ({
    level,
    x1: 100,
    y1,
    x2: 300,
    y2,
    startPrice: 0,
    endPrice: 0,
    endY: y2,
  });

  it("splits a line at the day it formed — faint before, solid after — on the drawn segment", () => {
    // (100, 50) → (300, 150)를 x 150에서 나누면 y = 50 + 100 × 0.25 = 75
    const { before, after } = formedSegments([segment(0, 50, 150)], 150);
    expect(before).toEqual([{ ...segment(0, 50, 150), x2: 150, y2: 75 }]);
    expect(after).toEqual([{ ...segment(0, 50, 150), x1: 150, y1: 75 }]);
    // 생긴 날이 선 앞이면 모두 그대로, 끝 뒤면 모두 옅게, 없으면 모두 그대로
    expect(formedSegments([segment(0, 50, 150)], 80).before).toEqual([]);
    expect(formedSegments([segment(0, 50, 150)], 320).after).toEqual([]);
    expect(formedSegments([segment(0, 50, 150)], undefined).after).toHaveLength(1);
  });

  it("fills a two-line horizontal zone as one band and keeps level shading for channels", () => {
    expect(shadedBands([segment(null, 40, 40), segment(null, 60, 60)])).toEqual([
      { tone: "zone", d: "M100.0 40.0L300.0 40.0L300.0 60.0L100.0 60.0Z" },
    ]);
    expect(shadedBands([segment(0, 40, 40), segment(1, 60, 60)]).map((band) => band.tone)).toEqual(["main"]);
  });
});

describe("level labels", () => {
  const plot = { left: 0, top: 12, right: 400, bottom: 200 };
  const label = (key: string, x: number, y: number) => ({ owner: "a", key, text: "0.5 (2,500.00)", x, y });

  it("anchors a label where the line enters the plot and drops lines outside it", () => {
    // (-50, 100) → (150, 200): 왼쪽 끝 x 0에서 y 125
    expect(labelAnchor({ x1: -50, y1: 100, x2: 150, y2: 200 }, plot)).toEqual({ x: 0, y: 125 });
    expect(labelAnchor({ x1: -80, y1: 100, x2: -10, y2: 100 }, plot)).toBeNull();
    expect(labelAnchor({ x1: 10, y1: 250, x2: 300, y2: 250 }, plot)).toBeNull();
  });

  it("pushes labels at the same place apart and keeps them inside the plot", () => {
    // 글자 14자 × 7.4 = 103.6px. 같은 x의 두 라벨은 바닥선 95·99 → 14px 간격으로 95·109
    const [first, second, right, high] = placeLevelLabels(
      [label("a", 100, 100), label("b", 100, 104), label("c", 380, 104), label("d", 250, 14)],
      plot,
    );
    expect([first.x, first.y, second.y]).toEqual([106, 95, 109]);
    // 오른쪽 끝을 넘는 라벨은 안으로(400 - 103.6 - 6), 가로로 떨어진 라벨은 밀지 않는다
    expect(right.x).toBeCloseTo(290.4);
    expect(right.y).toBe(99);
    // 위 끝을 넘는 바닥선(9)은 글자 높이만큼 안으로(12 + 11)
    expect(high.y).toBe(23);
  });
});

describe("dateTicks", () => {
  // 2015-01 ~ 2024-12 월봉 120개
  const months = Array.from({ length: 120 }, (_, index) => {
    const year = 2015 + Math.floor(index / 12);
    const month = String((index % 12) + 1).padStart(2, "0");
    return candle(`${year}-${month}-01`, `${year}-${month}-28`);
  });
  // 그림 칸 폭 = 전체 폭 - 가격 축 84px
  const scaleOf = (from: number, to: number, plotWidth: number) =>
    chartScale({ candles: months, view: { from, to }, width: plotWidth + 84, height: 228 });

  it("thins to whole years when many years share a narrow plot and drops ticks cut at the edges", () => {
    // 120봉을 240px에: 봉 간격 2px, 60달(120px)이 처음으로 64px를 넘는다 → 5년마다 해만.
    // 2015(x 1)는 왼쪽 끝 22px 안이라 잘리므로 빼고 2020(x 121)만 남는다
    expect(dateTicks(months, scaleOf(-0.5, 119.5, 240), 64)).toEqual([
      { index: 60, x: 121, label: "2020" },
    ]);
  });

  it("keeps months with the year on January and the first tick when bars are wide", () => {
    // 봉 10~16을 210px에: 봉 간격 30px, 3달(90px)마다
    expect(dateTicks(months, scaleOf(9.5, 16.5, 210), 64)).toEqual([
      { index: 12, x: 75, label: "2016.01" },
      { index: 15, x: 165, label: "04" },
    ]);
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
  const scale = chartScale({ candles: WEEKS, view: { from: -0.5, to: 4.5 }, width: 484, height: 228 });
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

// backend domain `_price_tick.py`가 응답(`tick_steps`)으로 보내는 표와 같은 모양의 손 픽스처
const STOCK_STEPS = [
  { below: 2_000, tick: 1 },
  { below: 5_000, tick: 5 },
  { below: 20_000, tick: 10 },
  { below: 50_000, tick: 50 },
  { below: 200_000, tick: 100 },
  { below: 500_000, tick: 500 },
  { below: null, tick: 1_000 },
];

describe("steppedPrice", () => {
  const stock = { kind: "stock" as const, tick_steps: STOCK_STEPS };

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

  it("reads the top band and the fund table from the response", () => {
    // 50만 원 이상은 1,000원 호가
    expect(steppedPrice(stock, 512_000, 1)).toBe(513_000);
    expect(steppedPrice(stock, 500_000, -1)).toBe(499_500);
    const fund = { kind: "stock" as const, tick_steps: [{ below: 2_000, tick: 1 }, { below: null, tick: 5 }] };
    expect(steppedPrice(fund, 10_000, 1)).toBe(10_005);
    expect(steppedPrice(fund, 1_999, 1)).toBe(2_000);
  });

  it("moves an index by 0.01 and snaps a 1% move onto the tick grid in the moving direction", () => {
    const index = { kind: "index" as const, tick_steps: [{ below: null, tick: 0.01 }] };
    expect(steppedPrice(index, 2_563.12, 1)).toBe(2_563.13);
    expect(steppedPrice(index, 2_500, 1, 1)).toBe(2_525);
    // 71,500 × 0.99 = 70,785 → 호가 100원 아래로 맞춰 70,700
    expect(steppedPrice(stock, 71_500, -1, 1)).toBe(70_700);
    // 12,345 × 1.01 = 12,468.45 → 호가 10원 위로 12,470
    expect(steppedPrice(stock, 12_345, 1, 1)).toBe(12_470);
  });

  it("counts from the displayed price when the saved price sits between ticks", () => {
    // 마그넷이 붙인 봉 값 2,726.7149는 2,726.71로 보인다 — ↑는 보이는 값에서 한 호가(2,726.72)
    const index = { kind: "index" as const, tick_steps: [{ below: null, tick: 0.01 }] };
    expect(steppedPrice(index, 2_726.7149, 1)).toBe(2_726.72);
    expect(steppedPrice(index, 2_726.7149, -1)).toBe(2_726.7);
  });

  it("never goes to zero or below", () => {
    expect(steppedPrice(stock, 1, -1)).toBe(1);
    expect(steppedPrice(stock, 1, -1, 1)).toBe(1);
    expect(steppedPrice(stock, 2, -1)).toBe(1);
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
      // 날짜가 앞서고 기간 버튼은 기본 보기로 되돌릴 기간으로 남는다
      period: "1y",
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

describe("switchSeries", () => {
  it("drops a zoomed date range when the timeframe or instrument changes and keeps the period button", () => {
    // 일봉에서 확대한 열흘 구간이 월봉에 물려 가면 봉 하나짜리 보기가 된다 — 고른 기간(1년)의 기본 보기로 연다
    const zoomed = { kind: "index" as const, code: "KOSPI", tf: "day" as const, period: "1y" as const, start: "2025-10-16", end: "2025-10-30" };
    expect(switchSeries(zoomed, { tf: "month" })).toEqual({ ...zoomed, tf: "month", start: undefined, end: undefined });
    expect(displayRange(switchSeries(zoomed, { kind: "stock", code: "005930" }), "2025-10-30")).toEqual({
      start: "2024-10-30",
      end: "2025-10-30",
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
