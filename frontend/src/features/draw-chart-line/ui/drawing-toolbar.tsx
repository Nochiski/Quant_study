import { t, type MessageKey } from "../../../shared/config";
import { Tooltip } from "../../../shared/ui";
import { DRAWING_TOOLS, type DrawingTool } from "../model/drawing";
import type { ChartDrawing } from "../model/use-chart-drawing";
import "./draw-chart-line.css";

// 아이콘은 같은 viewBox(20)·선 굵기를 쓴다. 이름은 aria-label과 툴팁이 준다.
const TOOL_ICONS: Record<DrawingTool, string> = {
  select: "M5 3l10 6.5-4.6 1.1 2.6 5.2-2 1-2.6-5.2L5 15z",
  hline: "M2 10h16M7 10a2 2 0 1 1-4 0a2 2 0 1 1 4 0",
  fib_retracement: "M3 4h14M3 8h14M3 12h14M3 16h14M6 4v12",
  fib_channel: "M2 14L14 4M6 18L18 8M4 16l12-10",
};
const MAGNET_ICON = "M4 3h4v7a2 2 0 0 0 4 0V3h4v7a6 6 0 0 1-12 0zM4 6.5h4M12 6.5h4";

const TOOL_LABELS: Record<DrawingTool, MessageKey> = {
  select: "chart.tool.select",
  hline: "chart.tool.hline",
  fib_retracement: "chart.tool.fib_retracement",
  fib_channel: "chart.tool.fib_channel",
};

const Icon = ({ d }: { d: string }) => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <path d={d} />
  </svg>
);

/** 48px 열의 세로 도구 막대. 버튼마다 접근 이름과 초점에서도 뜨는 툴팁이 있다. */
export const DrawingToolbar = ({ drawing }: { drawing: ChartDrawing }) => {
  const { state, dispatch } = drawing;
  return (
    <div
      className="drawing-toolbar"
      role="toolbar"
      aria-label={t("chart.tool.label")}
      aria-orientation="vertical"
    >
      {DRAWING_TOOLS.map((tool) => (
        <Tooltip key={tool} content={t(TOOL_LABELS[tool])}>
          <button
            type="button"
            className="drawing-toolbar__button"
            aria-label={t(TOOL_LABELS[tool])}
            aria-pressed={state.tool === tool}
            onClick={() => dispatch({ type: "pickTool", tool })}
          >
            <Icon d={TOOL_ICONS[tool]} />
          </button>
        </Tooltip>
      ))}
      <span className="drawing-toolbar__divider" aria-hidden="true" />
      <Tooltip content={t(state.magnet ? "chart.hint.magnetOn" : "chart.hint.magnetOff")}>
        <button
          type="button"
          className="drawing-toolbar__button"
          aria-label={t("chart.tool.magnet")}
          aria-pressed={state.magnet}
          onClick={() => dispatch({ type: "toggleMagnet" })}
        >
          <Icon d={MAGNET_ICON} />
        </button>
      </Tooltip>
    </div>
  );
};
