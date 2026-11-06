import type { ChartCandleBody, InstrumentKind, Timeframe } from "../../../shared/api";
import type { ChartTarget } from "./chart-queries";

/**
 * 개별종목차트 화면의 주소 칸(종목·주기·표시 기간) 해석의 owner. 뒤로 가기가 이전 조건을 그대로 되살리도록 조건은
 * 모두 주소가 갖는다. 모르는 값은 버리고 기본값으로 읽는다.
 */
export const CHART_PERIODS = ["6m", "1y", "2y", "5y"] as const;
export type ChartPeriod = (typeof CHART_PERIODS)[number];
export const CHART_TIMEFRAMES: readonly Timeframe[] = ["day", "week", "month"];

export const DEFAULT_TIMEFRAME: Timeframe = "week";
export const DEFAULT_PERIOD: ChartPeriod = "2y";

const PERIOD_MONTHS: Record<ChartPeriod, number> = { "6m": 6, "1y": 12, "2y": 24, "5y": 60 };

export type StockChartSearch = {
  kind?: InstrumentKind;
  code?: string;
  tf?: Timeframe;
  period?: ChartPeriod;
  /**
   * 직접 고르거나 줌·이동으로 정한 표시 기간(`YYYY-MM-DD`). 둘 다 있어야 하고 시작 ≤ 끝이다. 있으면 `period`보다
   * 앞서고, `period`는 기본 보기로 되돌릴 기간으로 남는다.
   */
  start?: string;
  end?: string;
};

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/u;

const isoDate = (value: unknown): string | undefined => {
  if (typeof value !== "string" || !ISO_DATE.test(value)) return undefined;
  const [year, month, day] = value.split("-").map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return parsed.getUTCMonth() === month - 1 && parsed.getUTCDate() === day ? value : undefined;
};

const oneOf = <T extends string>(values: readonly T[], value: unknown): T | undefined =>
  values.find((item) => item === value);

export const stockChartSearch = (search: Record<string, unknown>): StockChartSearch => {
  const kind = oneOf<InstrumentKind>(["stock", "index"], search.kind);
  const code = typeof search.code === "string" && search.code.trim() !== "" ? search.code : undefined;
  const start = isoDate(search.start);
  const end = isoDate(search.end);
  const custom = start !== undefined && end !== undefined && start <= end;
  return {
    ...(kind !== undefined && code !== undefined ? { kind, code } : {}),
    tf: oneOf(CHART_TIMEFRAMES, search.tf),
    period: oneOf(CHART_PERIODS, search.period),
    start: custom ? start : undefined,
    end: custom ? end : undefined,
  };
};

export const chartTargetOf = (search: StockChartSearch): ChartTarget | null =>
  search.kind !== undefined && search.code !== undefined
    ? { kind: search.kind, code: search.code }
    : null;

/** `date`에서 `months`달 앞의 같은 날. 그 달에 그날이 없으면 그 달 마지막 날이다(3-31 → 2-28). */
export const monthsBefore = (date: string, months: number): string => {
  const [year, month, day] = date.split("-").map(Number);
  const target = new Date(Date.UTC(year, month - 1 - months, 1));
  const lastDay = new Date(
    Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0),
  ).getUTCDate();
  target.setUTCDate(Math.min(day, lastDay));
  return target.toISOString().slice(0, 10);
};

/** 표시 기간. 기간 버튼은 기준일(`asOf`, 원장의 최신 확정 거래일)에서 거꾸로 센다 — 오늘 달력이 아니다. */
export const displayRange = (
  search: StockChartSearch,
  asOf: string,
): { start: string; end: string } =>
  search.start !== undefined && search.end !== undefined
    ? { start: search.start, end: search.end }
    : { start: monthsBefore(asOf, PERIOD_MONTHS[search.period ?? DEFAULT_PERIOD]), end: asOf };

/**
 * 표시 기간에 걸친 봉의 index 범위(포함). 봉 기간(`start`~`end`)이 표시 기간과 하루라도 겹치면 보인다. 겹치는
 * 봉이 없으면 null.
 */
export const visibleRange = (
  candles: readonly ChartCandleBody[],
  range: { start: string; end: string },
): { first: number; last: number } | null => {
  const first = candles.findIndex((candle) => candle.end >= range.start);
  if (first < 0 || candles[first].start > range.end) return null;
  let last = first;
  while (last + 1 < candles.length && candles[last + 1].start <= range.end) last += 1;
  return { first, last };
};

/**
 * 완성 봉(진행 중 봉을 뺀 앞부분). 어디까지가 완성인지는 응답의 `last_completed`가 정한다 — 기준점·좌표 칸은
 * 진행 중 봉을 쓸 수 없다(서버도 `chart.line.anchor_in_progress`로 거절한다).
 */
export const completedCandles = (
  candles: readonly ChartCandleBody[],
  lastCompleted: string | null,
): readonly ChartCandleBody[] => {
  if (lastCompleted === null) return [];
  let count = 0;
  while (count < candles.length && candles[count].start <= lastCompleted) count += 1;
  return candles.slice(0, count);
};
