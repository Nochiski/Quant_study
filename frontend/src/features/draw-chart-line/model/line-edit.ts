import {
  steppedPrice,
  type ChartInstrumentBody,
  type ChartLineBody,
  type ChartLineEditBody,
  type ChartPointBody,
  type CreateChartLineBody,
  type LineTone,
} from "../../../entities/chart";

/**
 * 저장 선 고치기 본문. `PUT /lines/{id}`는 칸 전체를 바꾸는 계약이라(빠진 칸은 기본값으로 돌아간다) 지금 선의
 * 칸을 모두 싣고 바꿀 칸만 덮는다. 레벨이 null(종류별 기본)이면 null 그대로 보낸다 — 기본 레벨을 화면이 펼쳐
 * 저장하지 않는다.
 */
export const chartLineEdit = (
  line: ChartLineBody,
  patch: Partial<ChartLineEditBody> = {},
): ChartLineEditBody => ({
  anchors: line.anchors,
  extend_right: line.extend_right,
  memo: line.memo,
  locked: line.locked,
  visible: line.visible,
  levels: line.levels,
  ...patch,
});

/** 지운 선을 같은 내용으로 다시 만드는 본문(되돌리기). 새 선은 새 id를 받는다. */
export const recreateChartLine = (line: ChartLineBody): CreateChartLineBody => ({
  instrument_kind: line.instrument_kind,
  instrument_code: line.instrument_code,
  timeframe: line.timeframe,
  kind: line.kind,
  source: line.source,
  anchors: line.anchors,
  levels: line.levels,
  extend_right: line.extend_right,
  memo: line.memo,
  locked: line.locked,
  visible: line.visible,
  auto: line.auto,
});

/** 되돌리기 쌓기. 마지막에 지운 것이 위다. 오래된 것부터 버려 `UNDO_LIMIT`개까지 둔다. */
export const UNDO_LIMIT = 20;
export type UndoStack = readonly CreateChartLineBody[];

export const pushUndo = (stack: UndoStack, line: ChartLineBody): UndoStack =>
  [...stack, recreateChartLine(line)].slice(-UNDO_LIMIT);

export const popUndo = (stack: UndoStack): [CreateChartLineBody | null, UndoStack] =>
  stack.length === 0 ? [null, stack] : [stack[stack.length - 1], stack.slice(0, -1)];

/**
 * 키보드로 옮긴 수평선 기준점. ↑/↓ = 한 호가, Shift = 1%. 수평선만 옮긴다(되돌림·채널은 기준점이 여럿이라
 * 카드에서 고친다). 잠긴 선과 자동 선(`source = auto`, 기준점 고정)은 null이다.
 */
export const nudgedAnchors = (
  line: Pick<ChartLineBody, "kind" | "anchors" | "locked" | "source">,
  instrument: Pick<ChartInstrumentBody, "kind" | "group">,
  direction: 1 | -1,
  large: boolean,
): ChartPointBody[] | null => {
  if (line.kind !== "hline" || line.locked || line.source === "auto") return null;
  return line.anchors.map((anchor) => ({
    bar_date: anchor.bar_date,
    price: steppedPrice(instrument, anchor.price, direction, large ? 1 : undefined),
  }));
};

/** 끌 수 있는 선인가: 잠기지 않았고, 자동 선이 아니고, 지금 보는 주기로 만든 선이다(다른 주기 기준점은 그 주기 화면에서). */
export const isDraggable = (
  line: Pick<ChartLineBody, "locked" | "source" | "timeframe">,
  viewTimeframe: ChartLineBody["timeframe"],
): boolean => !line.locked && line.source !== "auto" && line.timeframe === viewTimeframe;

/** 저장 선의 색 역할. 손으로 그었거나 자동 후보를 복사한 선은 내 선(잉크), 저장한 자동 후보는 종류 색이다. */
export const lineTone = (line: Pick<ChartLineBody, "source" | "kind">): LineTone =>
  line.source === "auto" ? `auto-${line.kind}` : "mine";
