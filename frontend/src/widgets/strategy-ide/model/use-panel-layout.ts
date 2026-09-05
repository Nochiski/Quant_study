import { useCallback, useEffect, useReducer, useState } from "react";

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

type PanelSizes = Pick<
  PanelLayout,
  "outlineWidth" | "inspectorWidth" | "debuggerHeight"
>;
type LayoutStorage = Pick<Storage, "getItem" | "setItem">;

export const PANEL_LAYOUT_STORAGE_KEY = "quant-workbench.panel-sizes.v1";

const browserStorage = (): LayoutStorage | null => {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
};

const validSize = (key: keyof PanelSizes, value: unknown): value is number =>
  Number.isInteger(value) &&
  (value as number) >= PANEL_BOUNDS[key].min &&
  (value as number) <= PANEL_BOUNDS[key].max;

export const readPanelSizes = (
  storage: LayoutStorage | null = browserStorage(),
): PanelSizes | null => {
  if (storage === null) return null;
  try {
    const value: unknown = JSON.parse(
      storage.getItem(PANEL_LAYOUT_STORAGE_KEY) ?? "null",
    );
    if (typeof value !== "object" || value === null || Array.isArray(value))
      return null;
    const record = value as { version?: unknown; sizes?: Partial<PanelSizes> };
    if (
      record.version !== 1 ||
      typeof record.sizes !== "object" ||
      record.sizes === null
    )
      return null;
    const { outlineWidth, inspectorWidth, debuggerHeight } = record.sizes;
    if (
      !validSize("outlineWidth", outlineWidth) ||
      !validSize("inspectorWidth", inspectorWidth) ||
      !validSize("debuggerHeight", debuggerHeight)
    )
      return null;
    return { outlineWidth, inspectorWidth, debuggerHeight };
  } catch {
    return null;
  }
};

const writePanelSizes = (
  storage: LayoutStorage | null,
  layout: PanelLayout,
): void => {
  try {
    storage?.setItem(
      PANEL_LAYOUT_STORAGE_KEY,
      JSON.stringify({
        version: 1,
        sizes: {
          outlineWidth: layout.outlineWidth,
          inspectorWidth: layout.inspectorWidth,
          debuggerHeight: layout.debuggerHeight,
        },
      }),
    );
  } catch {
    // Local persistence must never make the editor unusable.
  }
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
  const [storage] = useState(browserStorage);
  const [layout, dispatch] = useReducer(reduce, initial, (base) => ({
    ...base,
    ...readPanelSizes(storage),
  }));
  useEffect(() => {
    writePanelSizes(storage, layout);
  }, [layout, storage]);
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
