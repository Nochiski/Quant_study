import type { ChartCandleBody, DateRange } from "../../../entities/chart";

/**
 * 판정·분석 구간을 차트 위에서 고치는 규칙(테두리 끌기·키보드)의 owner. 구간은 완성 봉 index의 양 끝(포함)이고,
 * 요청에는 그 봉의 시작일을 싣는다 — 서버가 날짜를 그 날짜가 든 봉으로 옮긴다(`ChartSeries.index_of`). 판정의 최소
 * 봉 수(판정 불가 기준)는 서버 domain이 정하고 여기서는 다시 재지 않는다 — 구간은 시작 < 끝이면 된다.
 */
export type BarWindow = { start: number; end: number };
export type WindowEdge = "start" | "end";

/** 끄는 구간의 가장 좁은 폭(봉 수). 수익률이 하나라도 있어야 서버가 판정을 시도할 수 있다. */
export const MIN_WINDOW_BARS = 2;

/** Shift와 함께 누르면 이만큼 옮긴다(봉 수). */
export const LARGE_STEP_BARS = 10;

/**
 * 한쪽 테두리를 봉 `index`로 옮긴다. 완성 봉 범위(0 ~ `count` − 1) 안으로 자르고, 반대쪽 테두리를 넘지 못하게
 * `MIN_WINDOW_BARS` 폭을 남긴다. 봉이 그 폭보다 적으면 구간을 그대로 둔다.
 */
export const moveWindowEdge = (
  window: BarWindow,
  edge: WindowEdge,
  index: number,
  count: number,
): BarWindow => {
  if (count < MIN_WINDOW_BARS) return window;
  const at = Math.round(index);
  if (edge === "start") {
    const start = Math.min(Math.max(at, 0), window.end - (MIN_WINDOW_BARS - 1));
    return { ...window, start: Math.max(start, 0) };
  }
  const end = Math.max(
    Math.min(at, count - 1),
    window.start + (MIN_WINDOW_BARS - 1),
  );
  return { ...window, end: Math.min(end, count - 1) };
};

/** 키보드 이동: ←/→ 한 봉, Shift는 `LARGE_STEP_BARS`봉. 그 밖의 키는 null(처리하지 않음). */
export const nudgeWindowEdge = (
  window: BarWindow,
  edge: WindowEdge,
  key: string,
  shift: boolean,
  count: number,
): BarWindow | null => {
  const step = shift ? LARGE_STEP_BARS : 1;
  const delta =
    key === "ArrowLeft" ? -step : key === "ArrowRight" ? step : null;
  if (delta === null) return null;
  return moveWindowEdge(window, edge, window[edge] + delta, count);
};

/** 봉 시작일 → 완성 봉 index. 응답의 구간 날짜는 봉 시작일이다. 없으면 -1. */
const indexOfStart = (
  completed: readonly ChartCandleBody[],
  date: string,
): number => completed.findIndex((candle) => candle.start === date);

/** 응답 구간(봉 시작일 양 끝) → 완성 봉 index 구간. 봉을 찾지 못하면 null. */
export const barWindowOf = (
  completed: readonly ChartCandleBody[],
  range: DateRange,
): BarWindow | null => {
  const start = indexOfStart(completed, range.start);
  const end = indexOfStart(completed, range.end);
  return start < 0 || end < 0 ? null : { start, end };
};

/** 완성 봉 index 구간 → 요청에 실을 날짜(그 봉의 시작일). */
export const windowDates = (
  completed: readonly ChartCandleBody[],
  window: BarWindow,
): DateRange => ({
  start: completed[window.start].start,
  end: completed[window.end].start,
});

/**
 * 생성 패널의 기본 분석 구간: 지금 화면에 보이는 완성 봉(되돌림·수평 구간은 "기본값은 화면 기간"). 보이는 완성 봉이
 * `MIN_WINDOW_BARS`보다 적으면 null(서버 기본 구간).
 */
export const visibleWindow = (
  visible: readonly ChartCandleBody[],
): DateRange | null =>
  visible.length < MIN_WINDOW_BARS
    ? null
    : { start: visible[0].start, end: visible[visible.length - 1].start };
