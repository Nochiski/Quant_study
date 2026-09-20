import { useCallback, useEffect, useReducer, useState } from "react";

/**
 * Panel sizes and collapse flags of the Strategy IDE. Widget-local by design (WORKFLOW 2.6:
 * panel size is widget state, never URL or server state).
 */
export type PanelLayout = {
  outlineWidth: number;
  inspectorWidth: number;
  debuggerHeight: number;
  assistantWidth: number;
  outlineOpen: boolean;
  inspectorOpen: boolean;
  debuggerOpen: boolean;
  assistantOpen: boolean;
  /**
   * 오른쪽 두 패널(계약·AI) 중 마지막으로 펼친 쪽. 화면이 좁아 둘을 나란히 두면 편집기가 최소 폭
   * 아래로 내려갈 때, 나중에 연 쪽을 오버레이로 돌리는 판정에 쓴다. 저장하지 않는다.
   */
  lastOpenedRight: "inspectorOpen" | "assistantOpen" | null;
};

export const PANEL_BOUNDS = {
  outlineWidth: { min: 180, max: 420 },
  inspectorWidth: { min: 240, max: 520 },
  debuggerHeight: { min: 120, max: 480 },
  assistantWidth: { min: 280, max: 560 },
} as const;

export const DEFAULT_LAYOUT: PanelLayout = {
  outlineWidth: 240,
  inspectorWidth: 320,
  debuggerHeight: 220,
  assistantWidth: 360,
  outlineOpen: true,
  inspectorOpen: true,
  debuggerOpen: true,
  // AI 사이드바만 기본 접힘이다: 슬롯을 넘기지 않는 화면과 첫 방문의 레이아웃을 그대로 둔다(B-04).
  assistantOpen: false,
  lastOpenedRight: null,
};

type PanelSizes = Pick<
  PanelLayout,
  "outlineWidth" | "inspectorWidth" | "debuggerHeight"
>;
/**
 * 복원된 배치. 기존 세 패널 폭은 함께 유효해야 하고, 뒤에 추가된 AI 사이드바 값(`assistantWidth`·
 * `assistantOpen`)은 없으면 기본값으로 둔다 — 이 키를 모르던 v1 기록도 그대로 읽힌다.
 */
export type StoredLayout = PanelSizes &
  Partial<Pick<PanelLayout, "assistantWidth" | "assistantOpen">>;
type LayoutStorage = Pick<Storage, "getItem" | "setItem">;

export const PANEL_LAYOUT_STORAGE_KEY = "quant-workbench.panel-sizes.v1";

const browserStorage = (): LayoutStorage | null => {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
};

const validSize = (
  key: keyof typeof PANEL_BOUNDS,
  value: unknown,
): value is number =>
  Number.isInteger(value) &&
  (value as number) >= PANEL_BOUNDS[key].min &&
  (value as number) <= PANEL_BOUNDS[key].max;

export const readPanelSizes = (
  storage: LayoutStorage | null = browserStorage(),
): StoredLayout | null => {
  if (storage === null) return null;
  try {
    const value: unknown = JSON.parse(
      storage.getItem(PANEL_LAYOUT_STORAGE_KEY) ?? "null",
    );
    if (typeof value !== "object" || value === null || Array.isArray(value))
      return null;
    const record = value as {
      version?: unknown;
      sizes?: Partial<PanelLayout>;
      open?: { assistant?: unknown };
    };
    if (
      record.version !== 1 ||
      typeof record.sizes !== "object" ||
      record.sizes === null
    )
      return null;
    const { outlineWidth, inspectorWidth, debuggerHeight, assistantWidth } =
      record.sizes;
    if (
      !validSize("outlineWidth", outlineWidth) ||
      !validSize("inspectorWidth", inspectorWidth) ||
      !validSize("debuggerHeight", debuggerHeight)
    )
      return null;
    // 범위 밖 값은 기록 전체를 버린다(fail-closed). 없는 값만 기본값으로 떨어진다.
    if (assistantWidth !== undefined && !validSize("assistantWidth", assistantWidth))
      return null;
    const assistantOpen = record.open?.assistant;
    if (assistantOpen !== undefined && typeof assistantOpen !== "boolean")
      return null;
    return {
      outlineWidth,
      inspectorWidth,
      debuggerHeight,
      ...(assistantWidth === undefined ? {} : { assistantWidth }),
      ...(assistantOpen === undefined ? {} : { assistantOpen }),
    };
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
          assistantWidth: layout.assistantWidth,
        },
        // 펼침 상태 중 AI 사이드바만 저장한다: 나머지 패널은 기본 펼침이라 복원할 것이 없고,
        // 사이드바는 기본 접힘이라 열어 둔 선택이 새로고침마다 사라지면 안 된다.
        open: { assistant: layout.assistantOpen },
      }),
    );
  } catch {
    // Local persistence must never make the editor unusable.
  }
};

type SizePanel = keyof typeof PANEL_BOUNDS;
type OpenPanel =
  | "outlineOpen"
  | "inspectorOpen"
  | "debuggerOpen"
  | "assistantOpen";

type Action =
  | { type: "resize"; panel: SizePanel; value: number }
  | { type: "toggle"; panel: OpenPanel }
  | { type: "close"; panels: OpenPanel[] };

const clamp = (panel: SizePanel, value: number) =>
  Math.min(PANEL_BOUNDS[panel].max, Math.max(PANEL_BOUNDS[panel].min, value));

const reduce = (state: PanelLayout, action: Action): PanelLayout => {
  switch (action.type) {
    case "resize":
      return { ...state, [action.panel]: clamp(action.panel, action.value) };
    case "toggle": {
      const panel = action.panel;
      const open = !state[panel];
      const right = panel === "inspectorOpen" || panel === "assistantOpen";
      return {
        ...state,
        [panel]: open,
        lastOpenedRight:
          right && open ? panel : state.lastOpenedRight,
      };
    }
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
    resize: useCallback(
      (panel: SizePanel, value: number) =>
        dispatch({ type: "resize", panel, value }),
      [],
    ),
    // 단축키 리스너가 의존성으로 들고 있으므로 렌더마다 새 함수를 만들지 않는다(리스너 재설치 방지).
    toggle: useCallback(
      (panel: OpenPanel) => dispatch({ type: "toggle", panel }),
      [],
    ),
    close: useCallback(
      (panels: OpenPanel[]) => dispatch({ type: "close", panels }),
      [],
    ),
  };
};
