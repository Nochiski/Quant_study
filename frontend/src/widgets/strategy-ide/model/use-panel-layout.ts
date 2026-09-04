import { useCallback, useReducer } from "react";

/**
 * Panel sizes and collapse flags of the Strategy IDE. Widget-local by design (WORKFLOW 2.6:
 * panel size is widget state, never URL or server state).
 */
export type PanelLayout = {
  outlineWidth: number;
  inspectorWidth: number;
  debuggerHeight: number;
  outlineOpen: boolean;
  inspectorOpen: boolean;
  debuggerOpen: boolean;
};

export const PANEL_BOUNDS = {
  outlineWidth: { min: 180, max: 420 },
  inspectorWidth: { min: 240, max: 520 },
  debuggerHeight: { min: 120, max: 480 },
} as const;

export const DEFAULT_LAYOUT: PanelLayout = {
  outlineWidth: 240,
  inspectorWidth: 320,
  debuggerHeight: 220,
  outlineOpen: true,
  inspectorOpen: true,
  debuggerOpen: true,
};

type Action =
  | {
      type: "resize";
      panel: "outlineWidth" | "inspectorWidth" | "debuggerHeight";
      value: number;
    }
  | { type: "toggle"; panel: "outlineOpen" | "inspectorOpen" | "debuggerOpen" }
  | {
      type: "close";
      panels: ("outlineOpen" | "inspectorOpen" | "debuggerOpen")[];
    };

const clamp = (panel: keyof typeof PANEL_BOUNDS, value: number) =>
  Math.min(PANEL_BOUNDS[panel].max, Math.max(PANEL_BOUNDS[panel].min, value));

const reduce = (state: PanelLayout, action: Action): PanelLayout => {
  switch (action.type) {
    case "resize":
      return { ...state, [action.panel]: clamp(action.panel, action.value) };
    case "toggle":
      return { ...state, [action.panel]: !state[action.panel] };
    case "close":
      return action.panels.reduce(
        (next, panel) => ({ ...next, [panel]: false }),
        state,
      );
  }
};

export const usePanelLayout = (initial: PanelLayout = DEFAULT_LAYOUT) => {
  const [layout, dispatch] = useReducer(reduce, initial);
  return {
    layout,
    resize: (
      panel: Extract<Action, { type: "resize" }>["panel"],
      value: number,
    ) => dispatch({ type: "resize", panel, value }),
    toggle: (panel: Extract<Action, { type: "toggle" }>["panel"]) =>
      dispatch({ type: "toggle", panel }),
    close: useCallback(
      (panels: Extract<Action, { type: "close" }>["panels"]) =>
        dispatch({ type: "close", panels }),
      [],
    ),
  };
};
