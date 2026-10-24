import { browserStorage } from "../../../shared/lib/browser-storage";
import { useCallback, useEffect, useReducer, useState } from "react";


/** 패널 폭·열림은 위젯의 로컬 상태이며 URL이나 서버 상태를 복제하지 않는다. */
export type PanelLayout = {
  outlineWidth: number;
  inspectorWidth: number;
  /** v1 저장 기록과의 호환 값이다. 전용 추적 작업공간의 높이로 사용하지 않는다. */
  debuggerHeight: number;
  outlineOpen: boolean;
  inspectorOpen: boolean;
  debuggerOpen: boolean;
  /** AI 어시스턴트 창(떠 있는 창)이 열렸는가. */
  assistantOpen: boolean;
  /**
   * 사용자가 AI 창 열림을 직접 골랐는가(저장 기록에 있었거나 토글했다). 고른 것만 저장한다 — 좁은 화면에서
   * 정한 기본값(닫힘)이나 다른 조작이 덩달아 닫은 것은 기록하지 않아, 넓은 화면에 와서도 기본값이 이긴다.
   */
  assistantChosen: boolean;
};

export const PANEL_BOUNDS = {
  outlineWidth: { min: 180, max: 420 },
  inspectorWidth: { min: 240, max: 520 },
  // 과거 저장 기록의 유효성만 검사하며 새 작업공간 크기를 제한하지 않는다.
  debuggerHeight: { min: 120, max: Number.POSITIVE_INFINITY },
} as const;

export const DEFAULT_LAYOUT: PanelLayout = {
  outlineWidth: 240,
  inspectorWidth: 320,
  debuggerHeight: 220,
  outlineOpen: false,
  inspectorOpen: false,
  debuggerOpen: false,
  // 구조·계약은 사용자가 명시해서 연다. AI 창은 처음부터 열어 둔다(RD-04-A, 2026-10-19 결정 3).
  // 저장된 닫힘 선택은 그대로 존중한다 — 기본값은 저장 기록에 `assistant` 키가 없을 때만 쓰인다.
  assistantOpen: true,
  assistantChosen: false,
};

type PanelSizes = Pick<
  PanelLayout,
  "outlineWidth" | "inspectorWidth" | "debuggerHeight"
>;
/**
 * 복원된 배치. 기존 세 패널 폭은 함께 유효해야 하고, AI 창 열림(`assistantOpen`)은 없으면 기본값으로 둔다 —
 * 이 키를 모르던 v1 기록도 그대로 읽힌다. 옛 AI 열 폭(`sizes.assistantWidth`)은 읽지 않고 무시한다.
 */
export type StoredLayout = PanelSizes &
  Partial<Pick<PanelLayout, "assistantOpen" | "assistantChosen">>;
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
    const { outlineWidth, inspectorWidth, debuggerHeight } = record.sizes;
    if (
      !validSize("outlineWidth", outlineWidth) ||
      !validSize("inspectorWidth", inspectorWidth) ||
      !validSize("debuggerHeight", debuggerHeight)
    )
      return null;
    const assistantOpen = record.open?.assistant;
    if (assistantOpen !== undefined && typeof assistantOpen !== "boolean")
      return null;
    return {
      outlineWidth,
      inspectorWidth,
      debuggerHeight,
      ...(assistantOpen === undefined ? {} : { assistantOpen, assistantChosen: true }),
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
        },
        // 사용자가 직접 고른 AI 창 열림만 복원한다. 나머지는 첫 진입의 주본문 공간을 보장한다.
        ...(layout.assistantChosen ? { open: { assistant: layout.assistantOpen } } : {}),
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
    case "toggle":
      // AI 창을 사용자가 여닫으면 그 선택을 기록한다. 다른 조작이 덩달아 닫는 `close` 는 기록하지 않는다.
      return {
        ...state,
        [action.panel]: !state[action.panel],
        ...(action.panel === "assistantOpen" ? { assistantChosen: true } : {}),
      };
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
