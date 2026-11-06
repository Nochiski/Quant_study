import type { ChartPointBody, LineKind } from "../../../entities/chart";
import type { MessageKey } from "../../../shared/config";

/**
 * 수동 그리기의 화면 상태(도구·마그넷·찍은 점·고른 선)와 그 상태 바꾸기 규칙의 owner. 서버 데이터(저장 선)는
 * 담지 않는다 — 고른 선은 id만 둔다(`.claude/rules/frontend-api-state.md`).
 */
export type DrawingTool = "select" | LineKind;

export const DRAWING_TOOLS: readonly DrawingTool[] = [
  "select",
  "hline",
  "fib_retracement",
  "fib_channel",
];

/**
 * 도구마다 누르는 횟수: 수평선 1점, 되돌림 2점(먼저 = 레벨 1, 나중 = 레벨 0), 채널 3점(기준선 두 점 + 폭 기준점).
 * 서버가 기준점 수를 다시 검사한다(`chart.line.anchor_count`) — 이것은 몇 번 누르면 끝나는지만 정한다.
 */
export const CLICKS_PER_LINE: Record<LineKind, number> = {
  hline: 1,
  fib_retracement: 2,
  fib_channel: 3,
};

// 몇 번째 점을 누를 차례인지 알리는 안내 줄. 줄 수는 누르는 횟수(`CLICKS_PER_LINE`)와 같다 — 단위 테스트가 길이를
// 맞춰 본다.
export const STEP_HINTS: Record<LineKind, readonly MessageKey[]> = {
  hline: ["chart.hint.hline"],
  fib_retracement: ["chart.hint.fib_retracement.0", "chart.hint.fib_retracement.1"],
  fib_channel: ["chart.hint.fib_channel.0", "chart.hint.fib_channel.1", "chart.hint.fib_channel.2"],
};

export type DrawingState = {
  tool: DrawingTool;
  /** 기본 켜짐: 가장 가까운 봉의 시·고·저·종가에 붙인다. */
  magnet: boolean;
  /** 지금 그리는 선에 이미 찍은 점(화면 좌표). */
  points: readonly ChartPointBody[];
  selectedId: string | null;
};

export const INITIAL_DRAWING: DrawingState = {
  tool: "select",
  magnet: true,
  points: [],
  selectedId: null,
};

export type DrawingAction =
  | { type: "pickTool"; tool: DrawingTool }
  | { type: "toggleMagnet" }
  | { type: "addPoint"; point: ChartPointBody }
  | { type: "cancel" }
  /** 다른 종목·주기로 넘어갔다 — 찍은 점과 고른 선을 버린다(도구·마그넷은 남긴다). */
  | { type: "reset" }
  | { type: "select"; lineId: string | null }
  /** 선 하나를 다 그렸다 — 선택 도구로 돌아가 새 선을 고른다(만들기 응답의 id). */
  | { type: "finished"; lineId: string };

export const drawingReducer = (state: DrawingState, action: DrawingAction): DrawingState => {
  switch (action.type) {
    case "pickTool":
      return { ...state, tool: action.tool, points: [], selectedId: action.tool === "select" ? state.selectedId : null };
    case "toggleMagnet":
      return { ...state, magnet: !state.magnet };
    case "addPoint":
      return state.tool === "select" ? state : { ...state, points: [...state.points, action.point] };
    case "cancel":
      return { ...state, points: [] };
    case "reset":
      return { ...state, points: [], selectedId: null };
    case "select":
      return { ...state, selectedId: action.lineId };
    case "finished":
      return { ...state, tool: "select", points: [], selectedId: action.lineId };
  }
};

/**
 * 이 점을 더하면 선이 끝나는가 — 끝나면 저장할 기준점 전부, 아니면 null. 선택 도구에서는 늘 null.
 */
export const completedAnchors = (
  state: DrawingState,
  point: ChartPointBody,
): ChartPointBody[] | null => {
  if (state.tool === "select") return null;
  const anchors = [...state.points, point];
  return anchors.length === CLICKS_PER_LINE[state.tool] ? anchors : null;
};
