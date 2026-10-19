import { browserStorage } from "../../../shared/lib/browser-storage";
import { useCallback, useEffect, useReducer, useState } from "react";


/** 패널 폭·열림은 위젯의 로컬 상태이며 URL이나 서버 상태를 복제하지 않는다. */
export type PanelLayout = {
  outlineWidth: number;
  inspectorWidth: number;
  /** v1 저장 기록과의 호환 값이다. 전용 추적 작업공간의 높이로 사용하지 않는다. */
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
  // 과거 저장 기록의 유효성만 검사하며 새 작업공간 크기를 제한하지 않는다.
  debuggerHeight: { min: 120, max: Number.POSITIVE_INFINITY },
  assistantWidth: { min: 280, max: 560 },
} as const;

export const DEFAULT_LAYOUT: PanelLayout = {
  outlineWidth: 240,
  inspectorWidth: 320,
  debuggerHeight: 220,
  assistantWidth: 360,
  outlineOpen: false,
  inspectorOpen: false,
  debuggerOpen: false,
  // 구조·계약은 사용자가 명시해서 연다. AI 열은 처음부터 왼쪽에 열어 둔다(RD-04-A, 2026-10-19 결정 3).
  // 저장된 닫힘 선택은 그대로 존중한다 — 기본값은 저장 기록에 `assistant` 키가 없을 때만 쓰인다.
  assistantOpen: true,
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
        // 기존 AI 열림 선호만 복원한다. 나머지는 첫 진입의 주본문 공간을 보장한다.
        open: { assistant: layout.assistantOpen },
      }),
    );
  } catch {
    // 로컬 저장 실패가 편집을 막아서는 안 된다.
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
