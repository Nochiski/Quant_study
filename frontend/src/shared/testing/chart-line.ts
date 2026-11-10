import type { ChartLineBody } from "../api";

/** 테스트용 저장 선 한 줄 — 기본은 연습 코스피 주봉의 손으로 그은 수평선이고 `values`로 칸을 바꾼다. */
export const chartLineFixture = (id: string, values: Partial<ChartLineBody> = {}): ChartLineBody => ({
  line_id: id,
  instrument_kind: "index",
  instrument_code: "practice:KOSPI:코스피",
  timeframe: "week",
  kind: "hline",
  source: "manual",
  anchors: [{ bar_date: "2025-03-03", price: 2_500 }],
  levels: null,
  extend_right: true,
  memo: "",
  locked: false,
  visible: true,
  auto: null,
  created: null,
  created_at: "2026-11-06T00:00:00Z",
  updated_at: "2026-11-06T00:00:00Z",
  issue: null,
  paths: [],
  ...values,
});
