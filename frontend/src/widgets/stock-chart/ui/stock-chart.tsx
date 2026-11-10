import { useIsMutating, useQuery } from "@tanstack/react-query";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";

import {
  AxisBadges,
  CHART_LINE_MUTATION_KEY,
  CandlePlot,
  DEFAULT_TIMEFRAME,
  chartCandlesQuery,
  chartConfluenceQuery,
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
  widensRange,
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
  DrawingCursorLabels,
  DrawingHint,
  DrawingSurface,
  DrawingToolbar,
  MyLinesPanel,
  useChartDrawing,
  type DrawingTool,
} from "../../../features/draw-chart-line";
import { UndoNotice, useLineRemoval } from "../../../features/remove-chart-line";
import {
  AnalysisWindowOverlay,
  AutoLinesPanel,
  ConfluenceBrackets,
  FinderPanels,
  previewOverlays,
  useLevelFinder,
  visibleWindow,
} from "../../../features/find-chart-levels";
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
  last,
  titleId,
  titleRef,
  actions,
}: {
  instrument: ChartInstrumentBody;
  timeframe: Timeframe;
  /** 받은 계열의 마지막 봉(진행 중일 수 있다). */
  last: ChartCandleBody | undefined;
  titleId: string;
  titleRef: RefObject<HTMLHeadingElement | null>;
  /** 머리 오른쪽 끝(분석 방식 전환). */
  actions: ReactNode;
}) => {
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
      {actions}
    </div>
  );
};

/** 분석 방식: 자동 찾기(자동 후보·생성 패널) / 수동 그리기(도구 막대). 수동 중에는 자동 후보를 가린다. */
type ChartMode = "auto" | "manual";

const ModeSwitch = ({ mode, onMode }: { mode: ChartMode; onMode: (mode: ChartMode) => void }) => (
  <div className="chart-segmented stock-chart__mode" role="group" aria-label={t("chart.mode.label")}>
    <button type="button" aria-pressed={mode === "auto"} onClick={() => onMode("auto")}>
      {t("chart.mode.auto")}
    </button>
    <button type="button" aria-pressed={mode === "manual"} onClick={() => onMode("manual")}>
      {t("chart.mode.manual")}
    </button>
  </div>
);

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
  current: number | null,
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
  ...(current === null
    ? []
    : [
        {
          key: "current",
          y: yOf(current),
          text: formatPrice(current, instrumentKind),
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
  const removal = useLineRemoval(target);
  const drawing = useChartDrawing(target, timeframe, removal.removeLine);
  const finder = useLevelFinder(target, timeframe);
  const [mode, setMode] = useState<ChartMode>("auto");
  // 중첩에 내 선도 넣는가 — 요청 칸이라 선의 보이기 저장 칸을 바꾸지 않는다
  const [includeMine, setIncludeMine] = useState(true);
  const layersRef = useRef<HTMLElement>(null);

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
  // 머리는 받은 계열의 마지막 봉을 보인다. 현재가(점선·오른쪽 배지·"현재가 대비")는 응답 머리 값 하나다
  // — 과거 범위만 보여도 기준일 종가이고, 서버의 수평 구간 저항·지지 구분과 같은 값이다.
  const lastCandle = candles?.[candles.length - 1];
  const currentPrice = context?.current_price ?? null;
  const ready = candles !== undefined;
  const handledFocus = useRef(0);
  useEffect(() => {
    if (!ready || focusRequest === handledFocus.current) return;
    handledFocus.current = focusRequest;
    titleRef.current?.focus();
  }, [focusRequest, ready]);

  // 패널을 닫으면 레이어 칸이 다시 그려진 뒤 연 버튼(`data-finder-key`)으로 초점을 돌려준다. 그 버튼이 사라졌으면(지운
  // 줄의 근거 등) 자동 후보 칸 제목으로 간다.
  const { panel } = finder;
  useEffect(() => {
    if (panel.view !== "layers" || panel.focusKey === null) return;
    const root = layersRef.current;
    const opener = root?.querySelector<HTMLElement>(`[data-finder-key="${CSS.escape(panel.focusKey)}"]`);
    (opener ?? root?.querySelector<HTMLElement>(".auto-lines h2"))?.focus();
  }, [panel]);

  const instrumentForQuery = candlesQuery.data?.context.instrument;
  const confluence = useQuery(
    chartConfluenceQuery(
      mode === "auto" && instrumentForQuery !== undefined
        ? {
            instrument_kind: target.kind,
            instrument_code: target.code,
            lines: finder.results.inlineLines,
            include_mine: includeMine,
          }
        : null,
    ),
  );

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
  // 수동 그리기 중에는 자동 후보를 가린다(내 선만 그린다)
  const shownLines = lines.filter((line) => line.visible && (mode === "auto" || lineTone(line) === "mine"));
  const changeMode = (next: ChartMode) => {
    setMode(next);
    if (next === "auto") drawing.dispatch({ type: "pickTool", tool: "select" });
    else {
      finder.dispatch({ type: "reset" });
      const selected = lines.find((line) => line.line_id === drawing.state.selectedId);
      if (selected !== undefined && lineTone(selected) !== "mine") drawing.dispatch({ type: "select", lineId: null });
    }
  };
  // 그리기 도구를 고르면(자동 찾기 중이어도) 수동 그리기로 넘어간다 — 도구 막대는 두 방식 모두 보인다
  const pickTool = (tool: DrawingTool) => {
    if (tool !== "select" && mode === "auto") changeMode("manual");
    drawing.dispatch({ type: "pickTool", tool });
  };
  const creating = panel.view === "create";
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
            last={lastCandle}
            titleId={titleId}
            titleRef={titleRef}
            actions={<ModeSwitch mode={mode} onMode={changeMode} />}
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
          {/* 도구 막대는 두 방식 모두 늘 왼쪽에 있다(트레이딩뷰 관례) */}
          <div className="stock-chart__tools">
            <DrawingToolbar drawing={drawing} onPick={pickTool} />
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
                  <Button
                    size="small"
                    onClick={() => {
                      navigation.reset();
                      // 이 버튼은 봉이 그려지면 사라진다 — 초점을 차트 제목으로 옮겨 길을 잃지 않게 한다
                      titleRef.current?.focus();
                    }}
                  >
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
                // 고른 선 지우기 키가 먼저고, 아니면 줌·이동 키다
                onKeyDown={(event) => {
                  if (!drawing.chartKey(event, shownLines)) navigation.svgProps.onKeyDown(event);
                }}
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
                  current={currentPrice}
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
                    // 미리보기 후보는 저장 선과 같은 그리기·라벨 배치에 넣는다(라벨끼리 밀어낸다)
                    overlays={creating ? previewOverlays(finder.results.previews, scale) : []}
                  />
                  {creating ? (
                    <AnalysisWindowOverlay
                      scale={scale}
                      completed={completed}
                      window={finder.results.window}
                      onChange={(window) => finder.dispatch({ type: "edit", input: { window } })}
                      onEscape={() => finder.dispatch({ type: "close" })}
                    />
                  ) : null}
                  {mode === "auto" && confluence.data ? (
                    <ConfluenceBrackets groups={confluence.data.groups} scale={scale} />
                  ) : null}
                </g>
                <AxisBadges
                  plot={scale.plot}
                  badges={axisBadges(
                    badgeLine === undefined ? [] : [badgeLine],
                    (line) => levelSegments(line.paths, line.extend_right, scale),
                    currentPrice,
                    scale.yOfPrice,
                    instrument.kind,
                  )}
                />
                <DrawingCursorLabels drawing={drawing} scale={scale} instrumentKind={instrument.kind} />
              </svg>
            ) : null}
          </div>
        </div>
        <div className="stock-chart__foot">
          <DrawingHint drawing={drawing} />
          <p id={keysId} className="stock-chart__keys">
            {t("chart.view.keys")}
          </p>
          {range !== null && candles && hasCustomRange(search) && widensRange(candles, range) ? (
            <p className="stock-chart__keys">{t("chart.view.widened")}</p>
          ) : null}
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
            {mode === "auto" ? (
              <>
                <li>
                  <svg width="22" height="10" aria-hidden="true">
                    <rect x="0" y="1" width="22" height="8" className="stock-chart__swatch stock-chart__swatch--zone" />
                  </svg>
                  {t("chart.legend.zone")}
                </li>
                <li>
                  <svg width="22" height="8" aria-hidden="true">
                    <path d="M0 4H22" className="stock-chart__swatch stock-chart__swatch--retracement" />
                  </svg>
                  {t("chart.legend.retracement")}
                </li>
                <li>
                  <svg width="22" height="8" aria-hidden="true">
                    <path d="M0 4H22" className="stock-chart__swatch stock-chart__swatch--channel" />
                  </svg>
                  {t("chart.legend.channel")}
                </li>
                <li>
                  <svg width="10" height="14" aria-hidden="true">
                    <path d="M1 1H8V13H1" className="stock-chart__swatch stock-chart__swatch--confluence" />
                  </svg>
                  {t("chart.legend.confluence")}
                </li>
                <li>
                  <svg width="22" height="8" aria-hidden="true">
                    <path d="M0 4H22" className="stock-chart__swatch stock-chart__swatch--before" />
                  </svg>
                  {t("chart.legend.before")}
                </li>
                <li className="stock-chart__legend-note">{t("chart.legend.notSignal")}</li>
              </>
            ) : null}
          </ul>
        </div>
        <UndoNotice removal={removal} />
      </section>
      <aside className="stock-chart__layers" aria-label={t("chart.layers.label")} ref={layersRef}>
        {instrument === undefined ? null : panel.view === "layers" ? (
          <>
            <MyLinesPanel
              drawing={drawing}
              removing={removal.removing}
              instrument={instrument}
              lines={lines}
              timeframe={timeframe}
              completed={completed}
              visible={visibleCompleted}
              levelChoices={linesQuery.data?.level_choices ?? []}
            />
            <AutoLinesPanel
              finder={finder}
              lines={lines}
              instrument={instrument}
              timeframe={timeframe}
              manual={mode === "manual"}
              confluenceCount={confluence.data?.groups.length ?? null}
              includeMine={includeMine}
              // 자동 선도 고른 선일 수 있어(근거 패널·차트에서 고름) 지우면 고르기를 푸는 같은 경로로 지운다
              onRemove={drawing.remove}
              removing={removal.removing}
              screenWindow={visibleWindow(visibleCompleted)}
            />
          </>
        ) : (
          <FinderPanels
            finder={finder}
            target={target}
            timeframe={timeframe}
            instrument={instrument}
            current={currentPrice}
            lines={lines}
            levelChoices={linesQuery.data?.level_choices ?? []}
            confluence={confluence.data}
            confluenceError={confluence.error}
            includeMine={includeMine}
            onIncludeMine={setIncludeMine}
          />
        )}
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
