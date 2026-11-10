/**
 * 개별종목차트 명사(캔들·저장 선·종목 검색)의 입구. 조회·변이, 좌표 변환·표시 모델, 캔들·선 그림을 낸다. 가격과
 * 레벨 선을 그릴 점은 backend 응답이고 여기서는 화면 위치로 놓기만 한다(설계 문서 6장).
 */
export {
  CHART_LINE_MUTATION_KEY,
  chartCandlesQuery,
  chartChannelsQuery,
  chartConfluenceQuery,
  chartLineCreateBody,
  chartLineEdit,
  chartInstrumentSearchQuery,
  chartKey,
  chartLinePreviewQuery,
  chartLinesQuery,
  chartRegimeQuery,
  chartRetracementQuery,
  chartZonesQuery,
  useCreateChartLine,
  useDeleteChartLine,
  useUpdateChartLine,
  type ChartLinePatch,
  type ChartTarget,
} from "./model/chart-queries";
export {
  formatPrice,
  priceInputValue,
  priceTick,
  roundPrice,
  steppedPrice,
  type TickedInstrument,
} from "./model/chart-price";
export {
  DATE_AXIS_HEIGHT,
  PRICE_AXIS_WIDTH,
  barAtOrBefore,
  chartScale,
  dateTicks,
  dayNumber,
  fractionalIndex,
  priceTicks,
  type ChartScale,
  type PlotBox,
} from "./model/chart-scale";
export {
  CHART_PERIODS,
  CHART_TIMEFRAMES,
  DEFAULT_PERIOD,
  DEFAULT_TIMEFRAME,
  chartTargetOf,
  completedCandles,
  displayRange,
  hasCustomRange,
  monthsBefore,
  stockChartSearch,
  switchSeries,
  visibleRange,
  type ChartPeriod,
  type DateRange,
  type StockChartSearch,
} from "./model/chart-search";
export {
  defaultViewport,
  panViewport,
  viewportRange,
  visibleBars,
  wheelZoomFactor,
  widensRange,
  zoomViewport,
  type ChartViewport,
} from "./model/chart-viewport";
export {
  labelAnchor,
  placeLevelLabels,
  spreadLabels,
  type LevelLabel,
} from "./model/label-layout";
export {
  chartErrorSentence,
  chosenByLabel,
  lineIssueLabel,
  lineKindLabel,
  lineTitle,
  regimeVerdictLabel,
  structureLabel,
  swingChoiceLabel,
  timeframeLabel,
  trendDirectionLabel,
  windowSourceLabel,
  zoneSideLabel,
} from "./model/line-copy";
export {
  formedSegments,
  levelSegments,
  levelText,
  placeLineLabels,
  segmentLabels,
  shadedBands,
  type ChartOverlayLine,
  type LevelSegment,
} from "./model/line-geometry";
export { CANDLE_FIELDS, snapToCandle, type CandleField } from "./model/magnet";
export { AxisBadges, type AxisBadge } from "./ui/axis-badges";
export { CandlePlot } from "./ui/candle-plot";
export { lineTone, type LineTone } from "./model/line-tone";
export { ChartLineShape } from "./ui/line-shape";
export type {
  ChartAutoRecordBody,
  ChartCandleBody,
  ChartCandlesBody,
  ChartChannelsBody,
  ChartConfluenceBody,
  ChartConfluenceGroupBody,
  ChartConfluenceLineBody,
  ChartDrawingBody,
  ChartInstrumentBody,
  ChartLevelPathBody,
  ChartLineBody,
  ChartLineEditBody,
  ChartLinePreviewBody,
  ChartLinesBody,
  ChartPivotBody,
  ChartPointBody,
  ChartRegimeBody,
  ChartRetracementBody,
  ChartTickStepBody,
  ChartZoneBody,
  ChartZonesBody,
  CreateChartLineBody,
  InstrumentKind,
  LineKind,
  LineSource,
  RegimeVerdict,
  SwingChoice,
  Timeframe,
  TrendDirection,
} from "../../shared/api";
export { UNDO_LIMIT, popUndo, pushUndo, recreateChartLine, type UndoEntry, type UndoStack } from "./model/undo-stack";
export { useLineRemoval, type LineRemoval, type RemovalNotice } from "./model/use-line-removal";
export { UndoNotice } from "./ui/undo-notice";
