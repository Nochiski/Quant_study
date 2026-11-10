import type { ChartLineBody, CreateChartLineBody } from "../../../shared/api";
import { chartLineCreateBody } from "./chart-queries";

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
