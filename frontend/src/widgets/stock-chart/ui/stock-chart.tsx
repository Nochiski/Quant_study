import { useIsMutating, useQuery } from "@tanstack/react-query";
import { useEffect, useId, useMemo, useRef, useState, type RefObject } from "react";

import {
  AxisBadges,
  CHART_LINE_MUTATION_KEY,
  CandlePlot,
  DEFAULT_TIMEFRAME,
  chartCandlesQuery,
  chartErrorSentence,
  chartLinesQuery,
  chartTargetOf,
  completedCandles,
  displayRange,
  formatPrice,
  hasCustomRange,
  levelSegments,
  lineTone,
  timeframeLabel,
  type AxisBadge,
  type ChartCandleBody,
  type ChartInstrumentBody,
  type ChartLineBody,
  type ChartTarget,
  type DateRange,
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
import { useChartNavigation } from "../../../features/navigate-chart";
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

const percent = (value: number) => `${Math.abs(value).toFixed(2)}%`;

/** 차트 머리: 이름·주기, 마지막 봉의 시·고·저·종가와 직전 봉 대비 등락, 진행 중 봉 표시. */
const ChartHead = ({
  instrument,
  timeframe,
  current,
  titleId,
  titleRef,
}: {
  instrument: ChartInstrumentBody;
  timeframe: Timeframe;
  current: ChartCandleBody | undefined;
  titleId: string;
  titleRef: RefObject<HTMLHeadingElement | null>;
}) => {
  const last = current;
  // 등락률은 캔들 응답 값(직전 봉 종가 대비, application이 계산)을 그대로 보인다
  const change = last?.change_pct ?? null;
  const price = (value: number) => formatPrice(value, instrument.kind);
  return (
    <div className="stock-chart__head">
      <h2 id={titleId} ref={titleRef} tabIndex={-1}>
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
  /** 종목을 고를 때마다 늘어나는 수. 늘면 차트가 그려진 뒤 차트 제목으로 초점을 옮긴다. */
  focusRequest: number;
  /** 줌·이동이 멈춘 뒤 보이는 날짜 구간(null = 기본 보기로 되돌림). 주소를 바꾸되 이력을 쌓지 않는다. */
  onViewRange: (range: DateRange | null) => void;
};

/**
 * 오른쪽 축 배지: 현재가와 **한 선**(고른 선, 없으면 포인터를 올린 선)의 마지막 완성 봉 레벨 가격(`end.price`).
 * 다른 선은 선 위 라벨만 단다 — 선마다 배지를 달면 축에 레벨 수십 개가 쌓여 읽을 수 없다.
 */
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
      tone: lineTone(line),
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

const ChartBody = ({ target, search, timeframe, focusRequest, onViewRange }: ChartBodyProps) => {
  const titleId = useId();
  const titleRef = useRef<HTMLHeadingElement>(null);
  const clipId = useId();
  const keysId = useId();
  const box = useRef<HTMLDivElement>(null);
  const width = useElementWidth(box);
  const candlesQuery = useQuery(chartCandlesQuery(target, timeframe));
  const linesQuery = useQuery(chartLinesQuery(target));
  const drawing = useChartDrawing(target, timeframe);
  const removal = useLineRemoval(target);

  const candles = candlesQuery.data?.candles;
  const context = candlesQuery.data?.context;
  const range = context ? displayRange(search, context.as_of) : null;
  // 되돌릴 기본 보기: 고른 기간 버튼(없으면 기본 기간)을 기준일에서 센 기간
  const baseRange = context ? displayRange({ period: search.period }, context.as_of) : null;
  const height = chartHeight(width);
  const navigation = useChartNavigation({
    candles,
    series: `${target.kind}|${target.code}|${timeframe}`,
    range,
    baseRange,
    width,
    height,
    canDrag: drawing.state.tool === "select",
    onRange: onViewRange,
  });
  const scale = navigation.scale;
  const lines = linesQuery.data?.lines ?? [];
  const lastCompleted = candlesQuery.data?.last_completed ?? null;
  // 응답이 같으면 같은 배열 — 고른 선 카드의 봉 날짜 목록(수천 개)이 다시 그려지지 않는다
  const completed = useMemo(() => completedCandles(candles ?? [], lastCompleted), [candles, lastCompleted]);
  const visibleCompleted = scale ? completed.slice(scale.first, scale.last + 1) : [];
  // 현재가 = 계열의 마지막 봉(진행 중이면 그 봉) 종가. 점선·오른쪽 배지·머리가 이 하나를 쓴다.
  const current = candles?.[candles.length - 1];
  const ready = candles !== undefined;
  const handledFocus = useRef(0);
  useEffect(() => {
    if (!ready || focusRequest === handledFocus.current) return;
    handledFocus.current = focusRequest;
    titleRef.current?.focus();
  }, [focusRequest, ready]);

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
  // 포인터를 올린 선은 선택 도구일 때만 본다(그리기 도구로 바꾸면 선의 누를 자리가 사라져 떠남이 오지 않는다)
  const badgeLine =
    shownLines.find((line) => line.line_id === drawing.state.selectedId) ??
    (drawing.state.tool === "select"
      ? shownLines.find((line) => line.line_id === drawing.hoverLineId)
      : undefined);
  const svgLabel = instrument
    ? tFill("chart.svgLabel", { name: instrument.name, timeframe: timeframeLabel(timeframe) })
    : "";

  return (
    <div className="stock-chart__workspace">
      <section className="stock-chart__card" aria-labelledby={titleId}>
        {instrument && candles ? (
          <ChartHead
            instrument={instrument}
            timeframe={timeframe}
            current={current}
            titleId={titleId}
            titleRef={titleRef}
          />
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
            ) : navigation.empty ? (
              <div className="stock-chart__state">
                <p role="status">{t("chart.empty.range")}</p>
                {/* 상장 전 구간처럼 날짜 칸이 봉 없는 기간이면 고른 기간 버튼의 기본 보기로 돌아간다 */}
                {hasCustomRange(search) ? (
                  <Button size="small" onClick={navigation.reset}>
                    {t("chart.empty.reset")}
                  </Button>
                ) : null}
              </div>
            ) : scale !== null && candles && instrument ? (
              <svg
                className={`stock-chart__svg${navigation.panning ? " stock-chart__svg--panning" : ""}`}
                width={width}
                height={height}
                viewBox={`0 0 ${width} ${height}`}
                role="group"
                aria-label={svgLabel}
                aria-describedby={keysId}
                // 키보드 줌·이동(+/−·←/→·Home)을 받는 Tab 정지
                tabIndex={0}
                {...navigation.svgProps}
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
                <CandlePlot
                  candles={candles}
                  scale={scale}
                  instrumentKind={instrument.kind}
                  height={height}
                  current={current}
                />
                <g clipPath={`url(#${clipId})`}>
                  <DrawingSurface
                    drawing={drawing}
                    scale={scale}
                    candles={candles}
                    completedCount={completed.length}
                    instrumentKind={instrument.kind}
                    timeframe={timeframe}
                    lines={shownLines}
                  />
                </g>
                <AxisBadges
                  plot={scale.plot}
                  badges={axisBadges(
                    badgeLine === undefined ? [] : [badgeLine],
                    (line) => levelSegments(line.paths, line.extend_right, scale),
                    current,
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
          <p id={keysId} className="stock-chart__keys">
            {t("chart.view.keys")}
          </p>
          {range === null ? null : (
            <p className="sr-only" aria-live="polite">
              {tFill("chart.view.visible", range)}
            </p>
          )}
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
            levelChoices={linesQuery.data?.level_choices ?? []}
          />
        ) : null}
      </aside>
    </div>
  );
};

type StockChartProps = {
  search: StockChartSearch;
  onSearch: (next: StockChartSearch) => void;
  /** 줌·이동이 바꾼 표시 기간을 주소에 쓴다(이력을 쌓지 않는 replace). */
  onReplaceSearch: (next: StockChartSearch) => void;
};

/**
 * 개별종목차트 작업 칸: 조건 줄, 차트(캔들·내 선·그리기), 레이어 칸. 조건은 주소(`search`)가 갖고, 서버 값은
 * query cache가, 도구·고른 선·되돌리기 같은 화면 상태는 `features/draw-chart-line`이 갖는다.
 */
export const StockChart = ({ search, onSearch, onReplaceSearch }: StockChartProps) => {
  const target = chartTargetOf(search);
  const timeframe = search.tf ?? DEFAULT_TIMEFRAME;
  const candles = useQuery(chartCandlesQuery(target, timeframe));
  const context = candles.data?.context;
  const range = context ? displayRange(search, context.as_of) : null;
  // 종목을 고르면 차트가 그려진 뒤 차트 제목으로 초점을 옮긴다(검색 칸에 남아 있지 않게)
  const [focusRequest, setFocusRequest] = useState(0);
  const change = (next: StockChartSearch) => {
    if (next.kind !== search.kind || next.code !== search.code) setFocusRequest((count) => count + 1);
    onSearch(next);
  };
  return (
    <div className="stock-chart">
      <ChartConditions search={search} range={range} onSearch={change} />
      {target === null ? (
        <EmptyState
          title={t("chart.page.pickFirst.title")}
          description={t("chart.page.pickFirst.description")}
        />
      ) : (
        <ChartBody
          target={target}
          search={search}
          timeframe={timeframe}
          focusRequest={focusRequest}
          // 보기의 날짜 구간은 기간 버튼(`period`)을 남긴 채 쓴다 — 더블클릭·Home이 그 기간으로 되돌린다
          onViewRange={(view) => onReplaceSearch({ ...search, start: view?.start, end: view?.end })}
        />
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
