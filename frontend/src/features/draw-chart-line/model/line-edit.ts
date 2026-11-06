import {
  steppedPrice,
  type ChartLineBody,
  type ChartPointBody,
  type CreateChartLineBody,
  type TickedInstrument,
} from "../../../entities/chart";

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
 * 키보드로 옮긴 수평선 기준점. ↑/↓ = 한 호가, Shift = 1%(호가 눈금에 맞춤). 수평선만 옮긴다(되돌림·채널은
 * 기준점이 여럿이라 카드에서 고친다). 잠긴 선·자동 선(`source = auto`, 기준점 고정)이거나 더 옮길 수 없으면
 * (0 이하) null이다.
 */
export const nudgedAnchors = (
  line: Pick<ChartLineBody, "kind" | "anchors" | "locked" | "source">,
  instrument: TickedInstrument,
  direction: 1 | -1,
  large: boolean,
): ChartPointBody[] | null => {
  if (line.kind !== "hline" || line.locked || line.source === "auto") return null;
  const moved = line.anchors.map((anchor) => ({
    bar_date: anchor.bar_date,
    price: steppedPrice(instrument, anchor.price, direction, large ? 1 : undefined),
  }));
  return moved.every((anchor, index) => anchor.price === line.anchors[index].price) ? null : moved;
};

/** 끌 수 있는 선인가: 잠기지 않았고, 자동 선이 아니고, 지금 보는 주기로 만든 선이다(다른 주기 기준점은 그 주기 화면에서). */
export const isDraggable = (
  line: Pick<ChartLineBody, "locked" | "source" | "timeframe">,
  viewTimeframe: ChartLineBody["timeframe"],
): boolean => !line.locked && line.source !== "auto" && line.timeframe === viewTimeframe;

/** 글을 쓰는 칸(입력·메모)의 단축키(Ctrl+Z·Esc)는 그 칸의 것이다 — 차트 단축키로 가로채지 않는다. */
export const isTextEntry = (target: EventTarget | null): boolean =>
  target instanceof HTMLElement &&
  (target.isContentEditable ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    (target instanceof HTMLInputElement && !["checkbox", "radio", "button"].includes(target.type)));
