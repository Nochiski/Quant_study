import { useId } from "react";

import {
  CHART_PERIODS,
  CHART_TIMEFRAMES,
  DEFAULT_PERIOD,
  DEFAULT_TIMEFRAME,
  hasCustomRange,
  switchSeries,
  type ChartInstrumentBody,
  type DateRange,
  type StockChartSearch,
} from "../../../entities/chart";
import { t } from "../../../shared/config";
import { InstrumentSearch } from "./instrument-search";

type ChartConditionsProps = {
  search: StockChartSearch;
  /** 지금 보이는 표시 기간(기간 버튼이면 기준일에서 센 날짜). 캔들을 받기 전에는 null. */
  range: DateRange | null;
  onSearch: (next: StockChartSearch) => void;
};

/**
 * 조건 줄: 종목 검색 · 표시 기간(6개월/1년/2년/5년 + 시작·끝 날짜) · 캔들 주기(일/주/월). 값은 모두 주소가 갖고
 * 여기서는 바꾼 주소를 넘기기만 한다.
 */
export const ChartConditions = ({ search, range, onSearch }: ChartConditionsProps) => {
  const id = useId();
  const custom = hasCustomRange(search);
  const period = search.period ?? DEFAULT_PERIOD;
  const timeframe = search.tf ?? DEFAULT_TIMEFRAME;
  // 종목·주기를 바꾸면 날짜(앞 계열의 줌 구간)는 비우고 기간 버튼만 남긴다
  const pick = (instrument: ChartInstrumentBody) =>
    onSearch(switchSeries(search, { kind: instrument.kind, code: instrument.code }));
  const setDate = (edge: "start" | "end", value: string) => {
    if (range === null || value === "") return;
    // 기간 버튼(`period`)은 남긴다 — 날짜가 앞서고, 기본 보기로 되돌릴 때 그 기간을 쓴다
    onSearch({ ...search, ...range, [edge]: value });
  };
  return (
    <div className="chart-conditions" role="group" aria-label={t("chart.conditions.label")}>
      <InstrumentSearch onPick={pick} />
      <div className="chart-conditions__field">
        <span id={`${id}-period`} className="chart-conditions__label">
          {t("chart.period.label")}
        </span>
        <div className="chart-conditions__row" role="group" aria-labelledby={`${id}-period`}>
          <div className="chart-segmented">
            {CHART_PERIODS.map((item) => (
              <button
                key={item}
                type="button"
                aria-pressed={!custom && period === item}
                onClick={() => onSearch({ ...search, period: item, start: undefined, end: undefined })}
              >
                {t(`chart.period.${item}`)}
              </button>
            ))}
          </div>
          <label className="chart-conditions__date">
            <span className="sr-only">{t("chart.period.start")}</span>
            <input
              className="ui-input"
              type="date"
              value={range?.start ?? ""}
              max={range?.end}
              disabled={range === null}
              onChange={(event) => setDate("start", event.target.value)}
            />
          </label>
          <span aria-hidden="true">~</span>
          <label className="chart-conditions__date">
            <span className="sr-only">{t("chart.period.end")}</span>
            <input
              className="ui-input"
              type="date"
              value={range?.end ?? ""}
              min={range?.start}
              disabled={range === null}
              onChange={(event) => setDate("end", event.target.value)}
            />
          </label>
        </div>
      </div>
      <div className="chart-conditions__field">
        <span id={`${id}-tf`} className="chart-conditions__label">
          {t("chart.timeframe.label")}
        </span>
        <div className="chart-segmented" role="group" aria-labelledby={`${id}-tf`}>
          {CHART_TIMEFRAMES.map((item) => (
            <button
              key={item}
              type="button"
              aria-pressed={timeframe === item}
              aria-label={t(`chart.timeframe.${item}`)}
              onClick={() => onSearch(switchSeries(search, { tf: item }))}
            >
              {t(`chart.timeframe.short.${item}`)}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};
