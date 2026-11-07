import {
  chartLineCreateBody,
  steppedPrice,
  type ChartLineBody,
  type ChartPointBody,
  type CreateChartLineBody,
  type TickedInstrument,
} from "../../../entities/chart";

/** 지운 선을 같은 내용으로 다시 만드는 본문(되돌리기). 새 선은 새 id를 받는다. */
export const recreateChartLine = (line: ChartLineBody): CreateChartLineBody => chartLineCreateBody(line);

/**
 * 되돌리기 쌓기. 한 칸은 한 번에 지운 선 묶음(자동 수평 구간 한 벌은 여러 선을 함께 지운다)과 그 이름이고, 되돌리기 한
 * 번이 그 묶음을 함께 되살린다(알림이 무엇을 되살렸는지 이름으로 말한다). 마지막에 지운 것이 위다. 오래된 것부터 버려
 * `UNDO_LIMIT`칸까지 둔다.
 */
export const UNDO_LIMIT = 20;
export type UndoEntry = { title: string; bodies: readonly CreateChartLineBody[] };
export type UndoStack = readonly UndoEntry[];

export const pushUndo = (stack: UndoStack, lines: readonly ChartLineBody[], title: string): UndoStack =>
  [...stack, { title, bodies: lines.map(recreateChartLine) }].slice(-UNDO_LIMIT);

export const popUndo = (stack: UndoStack): [UndoEntry | null, UndoStack] =>
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
