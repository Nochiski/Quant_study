import type { ChartCandleBody } from "../../../shared/api";

/**
 * 차트 좌표 변환(날짜 ↔ x, 가격 ↔ y)의 owner. SVG를 px로 그리므로 여기 값이 곧 화면 위치다.
 *
 * x는 봉 순서(index)에 비례한다 — 휴장일·주말에 빈칸을 두지 않는 캔들 차트 관례다. 다른 주기로 만든 선의 날짜처럼
 * 봉 시작일이 아닌 날짜는 그 날짜가 든 봉 안에서 달력일 비율로 놓는다(설계 문서 4.7 좌표 계약: 화면은 원래 주기
 * 봉의 날짜를 날짜 축 위에서 잇는다). 가격은 계산하지 않고 위치만 바꾼다.
 */

/** 오른쪽 가격 축 칸과 아래 날짜 축 칸(px). 글자 12px 이상이 들어가는 폭이다. */
export const PRICE_AXIS_WIDTH = 84;
export const DATE_AXIS_HEIGHT = 28;
const PLOT_TOP = 12;
const PRICE_PADDING = 0.06;

export type PlotBox = { left: number; top: number; right: number; bottom: number };

export type ChartScale = {
  plot: PlotBox;
  /** 봉 하나의 폭(px). */
  step: number;
  /** 보이는 첫·마지막 봉 index(포함). */
  first: number;
  last: number;
  /** 마지막 봉 오른쪽의 미래 칸이 시작하는 x. */
  futureLeft: number;
  priceMin: number;
  priceMax: number;
  xOfIndex: (index: number) => number;
  xOfDate: (date: string) => number;
  /** x에 가장 가까운 봉 index(자르지 않는다). */
  indexAtX: (x: number) => number;
  yOfPrice: (price: number) => number;
  priceAtY: (y: number) => number;
};

const DAY_MS = 86_400_000;

/** `YYYY-MM-DD` → 1970-01-01부터 센 날 수(시간대 없음). */
export const dayNumber = (date: string): number => {
  const [year, month, day] = date.split("-").map(Number);
  return Date.UTC(year, month - 1, day) / DAY_MS;
};

/** 봉 시작일이 `date` 이하인 마지막 봉 index. 첫 봉보다 앞이면 -1. */
const barAtOrBefore = (candles: readonly ChartCandleBody[], date: string): number => {
  let low = 0;
  let high = candles.length - 1;
  let found = -1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (candles[middle].start <= date) {
      found = middle;
      low = middle + 1;
    } else high = middle - 1;
  }
  return found;
};

/** 봉 하나가 차지하는 달력일 — 마지막 봉 뒤(미래)나 첫 봉 앞을 같은 간격으로 늘릴 때 쓴다. */
const barSpanDays = (candles: readonly ChartCandleBody[], index: number): number => {
  const next = Math.min(index + 1, candles.length - 1);
  const previous = Math.max(next - 1, 0);
  const span = dayNumber(candles[next].start) - dayNumber(candles[previous].start);
  return span > 0 ? span : 1;
};

/** 봉 index(분수 포함) — 그 날짜가 든 봉 안에서는 시작일부터 센 달력일 비율이다. */
export const fractionalIndex = (candles: readonly ChartCandleBody[], date: string): number => {
  if (candles.length === 0) return 0;
  const at = barAtOrBefore(candles, date);
  if (at < 0) return -(dayNumber(candles[0].start) - dayNumber(date)) / barSpanDays(candles, 0);
  const span = barSpanDays(candles, at);
  const offset = (dayNumber(date) - dayNumber(candles[at].start)) / span;
  return at + (at < candles.length - 1 ? Math.min(offset, 1) : offset);
};

export type ChartScaleInput = {
  candles: readonly ChartCandleBody[];
  first: number;
  last: number;
  width: number;
  height: number;
  /** 미래(오른쪽 연장) 칸에 둘 봉 수. */
  futureBars: number;
};

/** 보이는 봉의 고가·저가 범위에 위아래 여유를 둔 가격 범위. 값이 하나뿐이면 ±1%. */
const priceRange = (
  candles: readonly ChartCandleBody[],
  first: number,
  last: number,
): [number, number] => {
  let low = Infinity;
  let high = -Infinity;
  for (let index = first; index <= last; index += 1) {
    low = Math.min(low, candles[index].low);
    high = Math.max(high, candles[index].high);
  }
  if (!Number.isFinite(low) || !Number.isFinite(high)) return [0, 1];
  if (high === low) return [low * 0.99, high * 1.01];
  const padding = (high - low) * PRICE_PADDING;
  return [low - padding, high + padding];
};

export const chartScale = ({
  candles,
  first,
  last,
  width,
  height,
  futureBars,
}: ChartScaleInput): ChartScale => {
  const plot: PlotBox = {
    left: 0,
    top: PLOT_TOP,
    right: Math.max(width - PRICE_AXIS_WIDTH, 1),
    bottom: Math.max(height - DATE_AXIS_HEIGHT, PLOT_TOP + 1),
  };
  const bars = last - first + 1 + futureBars;
  const step = (plot.right - plot.left) / Math.max(bars, 1);
  const [priceMin, priceMax] = priceRange(candles, first, last);
  const xOfIndex = (index: number) => plot.left + (index - first + 0.5) * step;
  const yOfPrice = (price: number) =>
    plot.bottom - ((price - priceMin) / (priceMax - priceMin)) * (plot.bottom - plot.top);
  return {
    plot,
    step,
    first,
    last,
    futureLeft: xOfIndex(last) + step / 2,
    priceMin,
    priceMax,
    xOfIndex,
    xOfDate: (date) => xOfIndex(fractionalIndex(candles, date)),
    indexAtX: (x) => Math.round((x - plot.left) / step - 0.5) + first,
    yOfPrice,
    priceAtY: (y) =>
      priceMin + ((plot.bottom - y) / (plot.bottom - plot.top)) * (priceMax - priceMin),
  };
};

/** 1·2·5 단계로 고른 가격 눈금(보이는 범위 안). 대략 `count`개. */
export const priceTicks = (min: number, max: number, count: number): number[] => {
  if (!(max > min) || count < 1) return [];
  const raw = (max - min) / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const unit = [1, 2, 5, 10].map((m) => m * magnitude).find((value) => value >= raw) ?? raw;
  // 눈금은 단위의 정수배로 센다 — 더하기를 거듭하면 꼬리(0.1 + 0.2)가 쌓여 끝 눈금이 빠진다
  const ticks: number[] = [];
  const epsilon = unit * 1e-9;
  for (let k = Math.ceil(min / unit - 1e-9); k * unit <= max + epsilon; k += 1) {
    ticks.push(Number((k * unit).toPrecision(12)));
  }
  return ticks;
};

export type DateTick = { index: number; label: string };

/**
 * 날짜 눈금: 보이는 봉 중 달이 바뀌는 첫 봉. 칸이 좁으면 `minGap`(px) 이상 떨어지게 1·3·6·12달 간격으로 솎는다.
 * 해가 바뀌는 눈금과 첫 눈금은 `2025.01`, 나머지는 `07`처럼 달만 쓴다.
 */
export const dateTicks = (
  candles: readonly ChartCandleBody[],
  scale: Pick<ChartScale, "first" | "last" | "step">,
  minGap: number,
): DateTick[] => {
  const starts: { index: number; year: number; month: number }[] = [];
  for (let index = scale.first; index <= scale.last; index += 1) {
    const [year, month] = candles[index].start.split("-").map(Number);
    const previous = index > 0 ? candles[index - 1].start.split("-").map(Number) : null;
    if (previous === null || previous[0] !== year || previous[1] !== month)
      starts.push({ index, year, month });
  }
  const months = starts.length;
  const span = Math.max(scale.last - scale.first + 1, 1);
  const barsPerMonth = months > 0 ? span / months : span;
  const stride = [1, 3, 6, 12].find((m) => m * barsPerMonth * scale.step >= minGap) ?? 12;
  return starts
    .filter((tick) => (tick.month - 1) % stride === 0)
    .map((tick, order) => {
      const month = String(tick.month).padStart(2, "0");
      // 첫 눈금에는 해를 붙여 어느 해인지 읽히게 한다
      return {
        index: tick.index,
        label: tick.month === 1 || order === 0 ? `${tick.year}.${month}` : month,
      };
    });
};
