import { useIsMutating, useQuery } from "@tanstack/react-query";
import { useId, useRef } from "react";

import {
  AxisBadges,
  CHART_LINE_MUTATION_KEY,
  CandlePlot,
  DEFAULT_TIMEFRAME,
  chartCandlesQuery,
  chartErrorSentence,
  chartLinesQuery,
  chartScale,
  chartTargetOf,
  displayRange,
  formatPrice,
  levelSegments,
  timeframeLabel,
  visibleRange,
  type AxisBadge,
  type ChartCandleBody,
  type ChartInstrumentBody,
  type ChartLineBody,
  type ChartTarget,
  type StockChartSearch,
  type Timeframe,
} from "../../../entities/chart";
import {
  DrawingHint,
  DrawingSurface,
  DrawingToolbar,
  MyLinesPanel,
  UndoNotice,
  useChartDrawing,
  useLineRemoval,
} from "../../../features/draw-chart-line";
import { failureReason } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { useElementWidth } from "../../../shared/lib/react";
import { Badge, Button, EmptyState, FailureNotice, SignedValue } from "../../../shared/ui";
import { ChartConditions } from "./chart-conditions";
import "./stock-chart.css";

const MIN_HEIGHT = 320;
const MAX_HEIGHT = 560;

/** 차트 높이: 칸 폭의 절반쯤, 320~560px. */
const chartHeight = (width: number) => Math.round(Math.min(Math.max(width * 0.52, MIN_HEIGHT), MAX_HEIGHT));

/** 오른쪽 연장할 미래 칸: 보이는 봉 수의 8%(적어도 3봉). */
const futureBars = (count: number) => Math.max(3, Math.round(count * 0.08));

const percent = (value: number) => `${Math.abs(value).toFixed(2)}%`;

/** 차트 머리: 이름·주기, 마지막 봉의 시·고·저·종가와 직전 봉 대비 등락, 진행 중 봉 표시. */
const ChartHead = ({
  instrument,
  timeframe,
  candles,
  titleId,
}: {
  instrument: ChartInstrumentBody;
  timeframe: Timeframe;
  candles: readonly ChartCandleBody[];
  titleId: string;
}) => {
  const last = candles[candles.length - 1];
  const previous = candles[candles.length - 2];
  const change = last !== undefined && previous !== undefined ? (last.close / previous.close - 1) * 100 : null;
  const price = (value: number) => formatPrice(value, instrument.kind);
  return (
    <div className="stock-chart__head">
      <h2 id={titleId}>
        {tFill("chart.head.title", { name: instrument.name, timeframe: timeframeLabel(timeframe) })}
      </h2>
      {last === undefined ? null : (
        <span className="stock-chart__ohlc">
          <span>{`${t("chart.head.open")} ${price(last.open)}`}</span>
          <span>{`${t("chart.head.high")} ${price(last.high)}`}</span>
          <span>{`${t("chart.head.low")} ${price(last.low)}`}</span>
          <span>{`${t("chart.head.close")} ${price(last.close)}`}</span>
          {change === null ? null : (
            <span title={t("chart.head.change")}>
              <SignedValue value={change} magnitude={percent(change)} />
            </span>
          )}
          {last.in_progress ? <Badge tone="neutral">{t("chart.head.inProgress")}</Badge> : null}
        </span>
      )}
    </div>
  );
};

type ChartBodyProps = {
  target: ChartTarget;
  search: StockChartSearch;
  timeframe: Timeframe;
};

/** 오른쪽 축 배지: 현재가와 보이는 선의 마지막 완성 봉 레벨 가격(`end.price`). */
const axisBadges = (
  lines: readonly ChartLineBody[],
  segmentsOf: (line: ChartLineBody) => ReturnType<typeof levelSegments>,
  current: ChartCandleBody | undefined,
  yOf: (price: number) => number,
  instrumentKind: ChartInstrumentBody["kind"],
): AxisBadge[] => [
  ...lines.flatMap((line) =>
    segmentsOf(line).map((segment, index) => ({
      key: `${line.line_id}-${index}`,
      y: segment.endY,
      text: formatPrice(segment.endPrice, instrumentKind),
      tone: line.source === "auto" ? ("auto" as const) : ("mine" as const),
    })),
  ),
  ...(current === undefined
    ? []
    : [
        {
          key: "current",
          y: yOf(current.close),
          text: formatPrice(current.close, instrumentKind),
          tone: "current" as const,
        },
      ]),
];

const ChartBody = ({ target, search, timeframe }: ChartBodyProps) => {
  const titleId = useId();
  const clipId = useId();
  const box = useRef<HTMLDivElement>(null);
  const width = useElementWidth(box);
  const candlesQuery = useQuery(chartCandlesQuery(target, timeframe));
  const linesQuery = useQuery(chartLinesQuery(target));
  const drawing = useChartDrawing(target, timeframe);
  const removal = useLineRemoval();

  const candles = candlesQuery.data?.candles;
  const context = candlesQuery.data?.context;
  const range = context ? displayRange(search, context.as_of) : null;
  const shown = candles && range ? visibleRange(candles, range) : null;
  const height = chartHeight(width);
  const scale =
    candles && shown && width > 0
      ? chartScale({
          candles,
          first: shown.first,
          last: shown.last,
          width,
          height,
          futureBars: futureBars(shown.last - shown.first + 1),
        })
      : null;
  const lines = linesQuery.data?.lines ?? [];
  const completed = (candles ?? []).filter((candle) => !candle.in_progress);
  const visibleCompleted =
    candles && shown
      ? candles.slice(shown.first, shown.last + 1).filter((candle) => !candle.in_progress)
      : [];

  if (candlesQuery.isError)
    return (
      <FailureNotice
        title={t("chart.error.load")}
        message={chartErrorSentence(candlesQuery.error)}
        reason={failureReason(candlesQuery.error)}
      >
        <Button size="small" onClick={() => void candlesQuery.refetch()}>
          {t("chart.retry")}
        </Button>
      </FailureNotice>
    );

  const instrument = context?.instrument;
  const shownLines = lines.filter((line) => line.visible);
  const svgLabel = instrument
    ? tFill("chart.svgLabel", { name: instrument.name, timeframe: timeframeLabel(timeframe) })
    : "";

  return (
    <div className="stock-chart__workspace">
      <section className="stock-chart__card" aria-labelledby={titleId}>
        {instrument && candles ? (
          <ChartHead instrument={instrument} timeframe={timeframe} candles={candles} titleId={titleId} />
        ) : (
          <div className="stock-chart__head">
            <h2 id={titleId}>{t("chart.loading")}</h2>
          </div>
        )}
        {context?.practice_data ? (
          <p className="stock-chart__practice">
            <Badge tone="warn">{t("chart.practice.badge")}</Badge> {context.notice}
          </p>
        ) : null}
        <div className="stock-chart__frame">
          <div className="stock-chart__tools">
            <DrawingToolbar drawing={drawing} />
          </div>
          <div className="stock-chart__plot" ref={box}>
            {candlesQuery.isPending ? (
              <p className="stock-chart__state" role="status">
                {t("chart.loading")}
              </p>
            ) : shown === null ? (
              <p className="stock-chart__state" role="status">
                {t("chart.empty.range")}
              </p>
            ) : scale !== null && candles && instrument ? (
              <svg
                className="stock-chart__svg"
                width={width}
                height={height}
                viewBox={`0 0 ${width} ${height}`}
                role="group"
                aria-label={svgLabel}
              >
                <defs>
                  <clipPath id={clipId}>
                    <rect
                      x={scale.plot.left}
                      y={scale.plot.top}
                      width={scale.plot.right - scale.plot.left}
                      height={scale.plot.bottom - scale.plot.top}
                    />
                  </clipPath>
                </defs>
                <CandlePlot candles={candles} scale={scale} instrumentKind={instrument.kind} height={height} />
                <g clipPath={`url(#${clipId})`}>
                  <DrawingSurface
                    drawing={drawing}
                    scale={scale}
                    candles={candles}
                    instrumentKind={instrument.kind}
                    timeframe={timeframe}
                    lines={shownLines}
                  />
                </g>
                <AxisBadges
                  plot={scale.plot}
                  badges={axisBadges(
                    shownLines,
                    (line) => levelSegments(line.paths, line.extend_right, scale),
                    candles[candles.length - 1],
                    scale.yOfPrice,
                    instrument.kind,
                  )}
                />
              </svg>
            ) : null}
          </div>
        </div>
        <div className="stock-chart__foot">
          <DrawingHint drawing={drawing} />
          <ul className="stock-chart__legend" aria-label={t("chart.legend.label")}>
            <li>
              <svg width="22" height="8" aria-hidden="true">
                <path d="M0 4H22" className="stock-chart__swatch stock-chart__swatch--mine" />
              </svg>
              {t("chart.legend.mine")}
            </li>
            <li>
              <svg width="22" height="8" aria-hidden="true">
                <path d="M0 4H22" className="stock-chart__swatch stock-chart__swatch--current" />
              </svg>
              {t("chart.legend.current")}
            </li>
            <li>
              <svg width="10" height="14" aria-hidden="true">
                <rect x="1" y="1" width="8" height="12" className="stock-chart__swatch stock-chart__swatch--live" />
              </svg>
              {t("chart.legend.inProgress")}
            </li>
          </ul>
        </div>
        <UndoNotice removal={removal} />
      </section>
      <aside className="stock-chart__layers" aria-label={t("chart.layers.label")}>
        {instrument ? (
          <MyLinesPanel
            drawing={drawing}
            removal={removal}
            instrument={instrument}
            lines={lines}
            timeframe={timeframe}
            completed={completed}
            visible={visibleCompleted}
          />
        ) : null}
      </aside>
    </div>
  );
};

type StockChartProps = {
  search: StockChartSearch;
  onSearch: (next: StockChartSearch) => void;
};

/**
 * 개별종목차트 작업 칸: 조건 줄, 차트(캔들·내 선·그리기), 레이어 칸. 조건은 주소(`search`)가 갖고, 서버 값은
 * query cache가, 도구·고른 선·되돌리기 같은 화면 상태는 `features/draw-chart-line`이 갖는다.
 */
export const StockChart = ({ search, onSearch }: StockChartProps) => {
  const target = chartTargetOf(search);
  const timeframe = search.tf ?? DEFAULT_TIMEFRAME;
  const candles = useQuery(chartCandlesQuery(target, timeframe));
  const context = candles.data?.context;
  const range = context ? displayRange(search, context.as_of) : null;
  return (
    <div className="stock-chart">
      <ChartConditions search={search} range={range} onSearch={onSearch} />
      {target === null ? (
        <EmptyState
          title={t("chart.page.pickFirst.title")}
          description={t("chart.page.pickFirst.description")}
        />
      ) : (
        <ChartBody target={target} search={search} timeframe={timeframe} />
      )}
    </div>
  );
};

/** 머리의 데이터 기준일 한 줄. 대상을 고르기 전에는 그리지 않는다. */
export const ChartDataBasis = ({ search }: { search: StockChartSearch }) => {
  const target = chartTargetOf(search);
  const candles = useQuery(chartCandlesQuery(target, search.tf ?? DEFAULT_TIMEFRAME));
  const asOf = candles.data?.context.as_of;
  return asOf === undefined ? null : (
    <p className="stock-chart__basis">{tFill("chart.page.basis", { date: asOf })}</p>
  );
};

/** 저장 상태 줄 "코스피에 그린 선 N개 · 자동 저장됨". 선 변이가 나가 있으면 저장 중이다. */
export const ChartSaveStatus = ({ search }: { search: StockChartSearch }) => {
  const target = chartTargetOf(search);
  const lines = useQuery(chartLinesQuery(target));
  const saving = useIsMutating({ mutationKey: CHART_LINE_MUTATION_KEY }) > 0;
  const data = lines.data;
  if (data === undefined) return null;
  const name = data.context.instrument.name;
  return (
    <p className="stock-chart__status" role="status">
      <span className="stock-chart__status-dot" aria-hidden="true" />
      {saving
        ? tFill("chart.save.saving", { name })
        : tFill("chart.save.status", { name, count: data.lines.length })}
    </p>
  );
};
