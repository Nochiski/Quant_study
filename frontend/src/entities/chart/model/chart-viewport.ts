import type { ChartCandleBody } from "../../../shared/api";
import { visibleRange, type DateRange } from "./chart-search";

/**
 * 차트 보기 창(줌·이동 상태) 계산의 owner. TradingView 관례를 따른다 — 휠은 커서 자리를 붙든 채 봉 간격을 바꾸고,
 * 끌기·가로 스크롤은 좌우로 옮기며, 기본 보기는 표시 기간의 마지막 봉을 오른쪽에 붙이고 미래 봉 여백을 둔다.
 *
 * 보기 창은 봉 index 좌표의 구간이다: `from`은 그림 칸 왼쪽 끝, `to`는 오른쪽 끝의 index다. 봉 `i`는
 * `[i - 0.5, i + 0.5]`를 차지하므로(봉 가운데가 정수) 봉 간격(px) = 그림 칸 폭 ÷ (`to - from`)이다. 폭과 무관한
 * 값이라 칸 폭이 바뀌어도 같은 봉 구간이 보인다.
 */
export type ChartViewport = { from: number; to: number };

/** 가장 크게 확대했을 때 보이는 봉 칸 수. */
const MIN_VISIBLE_BARS = 10;
/** 가장 작게 축소했을 때 보이는 봉 칸 수 = 계열 봉 수 × 이 배수(앞뒤 빈칸 여유). 봉이 적으면 기본 보기 폭 이상. */
const MAX_SPAN_RATIO = 1.25;
/** 한쪽 끝에 비워 둘 수 있는 칸의 최대 비율 — 계열을 화면 밖으로 다 밀어내지 않는다. */
const MAX_EMPTY_RATIO = 0.5;

/** 오른쪽 연장(채널·되돌림)을 보일 미래 칸: 보이는 봉 수의 8%(적어도 3봉). */
export const futureBars = (count: number): number => Math.max(3, Math.round(count * 0.08));

// 보기 창 폭(봉 칸 수)의 한도. 최대는 계열 전부 + 미래 여백(기본 보기)보다 좁지 않다 — 봉이 적은 계열에서
// 기본 보기보다 좁으면 축소하려던 휠이 오히려 확대로 뒤집힌다.
const spanLimits = (count: number) => {
  const min = Math.min(MIN_VISIBLE_BARS, Math.max(count, 1));
  const max = Math.max(Math.ceil(count * MAX_SPAN_RATIO), count + futureBars(count));
  return { min, max: Math.max(min, max) };
};

/** 보기 창에 조금이라도 걸친 봉 index 범위(포함). 계열 밖은 자른다 — 걸친 봉이 없으면 `first > last`다. */
export const visibleBars = (
  view: ChartViewport,
  count: number,
): { first: number; last: number } => ({
  first: Math.max(0, Math.floor(view.from - 0.5) + 1),
  last: Math.min(count - 1, Math.ceil(view.to + 0.5) - 1),
});

/**
 * 표시 기간의 기본 보기: 그 기간에 걸친 첫 봉 ~ 마지막 봉을 꼭 맞게 넣고, 마지막 봉이 계열의 끝(최신 봉)이면
 * 오른쪽에 미래 봉 여백을 둔다(최신 봉 오른쪽 정렬). 과거 구간을 고른 날짜 기간은 여백 없이 끝 봉에서 끝난다.
 * 폭은 확대·축소와 같은 한도 안이다 — 봉이 몇 개뿐인 기간은 오른쪽 끝을 둔 채 최소 칸 수로 넓힌다. 기간에 걸친
 * 봉이 없으면 null.
 *
 * 미래 여백도 한쪽 빈칸 한도(보기의 절반) 안이다. 봉이 한둘인 계열에서 "적어도 3봉" 여백을 그대로 두면 한도에
 * 걸려 보기가 왼쪽으로 밀리고 최신 봉이 오른쪽 정렬에서 한 칸 벗어난다 — 그래서 여백을 한도까지 줄인다.
 */
export const defaultViewport = (
  candles: readonly ChartCandleBody[],
  range: DateRange,
): ChartViewport | null => {
  const shown = visibleRange(candles, range);
  if (shown === null) return null;
  const { min, max } = spanLimits(candles.length);
  const bars = shown.last - shown.first + 1;
  // 여백 m의 빈칸이 보기(봉 + m, 최소 폭 이상)의 절반을 넘지 않는 가장 큰 값: m ≤ 봉 수, 또는 최소 폭의 절반
  const marginLimit = Math.max(bars, Math.floor(min * MAX_EMPTY_RATIO));
  const margin = shown.last === candles.length - 1 ? Math.min(futureBars(bars), marginLimit) : 0;
  const to = shown.last + 0.5 + margin;
  const span = Math.min(Math.max(to - (shown.first - 0.5), min), max);
  return keepSeriesInView({ from: to - span, to }, candles.length);
};

/** 한쪽 끝의 빈칸이 보기 창의 절반을 넘지 않게 좌우로 민다(폭은 그대로). */
const keepSeriesInView = (view: ChartViewport, count: number): ChartViewport => {
  const span = view.to - view.from;
  const minFrom = -0.5 - span * MAX_EMPTY_RATIO;
  const maxTo = count - 0.5 + span * MAX_EMPTY_RATIO;
  const shift = view.from < minFrom ? minFrom - view.from : view.to > maxTo ? maxTo - view.to : 0;
  return { from: view.from + shift, to: view.to + shift };
};

/**
 * 확대·축소. `factor` > 1은 축소(더 많은 봉), < 1은 확대다. `anchor`는 그림 칸 폭에 대한 커서 자리 비율(0 = 왼쪽
 * 끝, 1 = 오른쪽 끝)이고 그 자리의 봉은 화면에서 움직이지 않는다. 보이는 봉 칸 수는 `MIN_VISIBLE_BARS` ~ 계열의
 * 1.25배로 자른다.
 */
export const zoomViewport = (
  view: ChartViewport,
  factor: number,
  anchor: number,
  count: number,
): ChartViewport => {
  const { min, max } = spanLimits(count);
  const span = view.to - view.from;
  const next = Math.min(Math.max(span * factor, min), max);
  const at = Math.min(Math.max(anchor, 0), 1);
  const pivot = view.from + at * span;
  const from = pivot - at * next;
  return keepSeriesInView({ from, to: from + next }, count);
};

/** 좌우 이동. `bars` > 0은 오른쪽(더 최근 봉)으로 보기 창을 옮긴다. */
export const panViewport = (view: ChartViewport, bars: number, count: number): ChartViewport =>
  keepSeriesInView({ from: view.from + bars, to: view.to + bars }, count);

/**
 * 보기 창에 보이는 날짜 구간: 걸친 첫 봉의 시작일 ~ 마지막 봉의 끝일. 표시 기간 칸·주소에 그대로 쓰며 이 구간을
 * 다시 `visibleRange`에 넣으면 같은 봉 범위가 나온다. 걸친 봉이 없으면 null.
 */
export const viewportRange = (
  view: ChartViewport,
  candles: readonly ChartCandleBody[],
): DateRange | null => {
  const { first, last } = visibleBars(view, candles.length);
  return first > last ? null : { start: candles[first].start, end: candles[last].end };
};

/**
 * 표시 기간이 최소 폭보다 짧아 기본 보기가 그 앞 봉까지 넓혀 보이는가 — 날짜 칸(이틀 등)과 화면 봉 수가 다른 까닭을
 * 안내할 때 쓴다. 기간에 걸친 봉이 없으면 false.
 */
export const widensRange = (candles: readonly ChartCandleBody[], range: DateRange): boolean => {
  const shown = visibleRange(candles, range);
  return shown !== null && shown.last - shown.first + 1 < spanLimits(candles.length).min;
};

/** 휠 한 번(px 단위 `deltaY`)의 배율. 마우스 한 칸(100px)이 약 16%이고 한 번에 2배를 넘지 않는다. */
export const wheelZoomFactor = (deltaY: number): number =>
  Math.exp(Math.min(Math.max(deltaY, -460), 460) * 0.0015);
