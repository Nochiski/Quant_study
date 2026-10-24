/**
 * 화면 위에 뜨는 창의 위치·크기 계산. 끌어 옮기기·가장자리 크기 조절·창 크기 변경 보정·저장값 복원이 모두 이
 * 순수 함수를 거친다 — 경계 규칙(최소·최대 크기, 뷰포트 밖으로 나가지 않음)이 한 곳에 있다. DOM·React 를 모른다.
 */

export type WindowRect = { x: number; y: number; width: number; height: number };
export type WindowLimits = {
  minWidth: number;
  minHeight: number;
  maxWidth: number;
  maxHeight: number;
};
export type Viewport = { width: number; height: number };
/** 끌고 있는 가장자리. 모서리는 두 변을 함께 켠다. */
export type ResizeEdges = {
  left?: boolean;
  right?: boolean;
  top?: boolean;
  bottom?: boolean;
};
/** 키보드 대안으로 고르는 크기. */
export type WindowSizePreset = "small" | "medium" | "large";

/** 창과 뷰포트 가장자리 사이에 늘 남기는 여백(px). */
export const WINDOW_MARGIN = 8;

const clamp = (value: number, low: number, high: number): number =>
  Math.min(Math.max(value, low), Math.max(low, high));

/** 지금 뷰포트에서 허용되는 폭·높이 범위. 뷰포트가 최소 크기보다 작으면 뷰포트가 이긴다. */
export const sizeBounds = (
  viewport: Viewport,
  limits: WindowLimits,
): { minWidth: number; maxWidth: number; minHeight: number; maxHeight: number } => {
  const maxWidth = Math.max(0, Math.min(limits.maxWidth, viewport.width - 2 * WINDOW_MARGIN));
  const maxHeight = Math.max(0, Math.min(limits.maxHeight, viewport.height - 2 * WINDOW_MARGIN));
  return {
    minWidth: Math.min(limits.minWidth, maxWidth),
    maxWidth,
    minHeight: Math.min(limits.minHeight, maxHeight),
    maxHeight,
  };
};

/** 크기를 범위 안으로, 위치를 뷰포트 안으로 맞춘다. 창 크기가 바뀌었을 때의 보정도 이것이다. */
export const fitRect = (
  rect: WindowRect,
  viewport: Viewport,
  limits: WindowLimits,
): WindowRect => {
  const bounds = sizeBounds(viewport, limits);
  const width = clamp(rect.width, bounds.minWidth, bounds.maxWidth);
  const height = clamp(rect.height, bounds.minHeight, bounds.maxHeight);
  return {
    width,
    height,
    x: clamp(rect.x, WINDOW_MARGIN, viewport.width - WINDOW_MARGIN - width),
    y: clamp(rect.y, WINDOW_MARGIN, viewport.height - WINDOW_MARGIN - height),
  };
};

/** 제목 줄을 끌어 옮긴다. 크기는 그대로이고 뷰포트 밖으로 나가지 않는다. */
export const moveRect = (
  start: WindowRect,
  dx: number,
  dy: number,
  viewport: Viewport,
  limits: WindowLimits,
): WindowRect => fitRect({ ...start, x: start.x + dx, y: start.y + dy }, viewport, limits);

/**
 * 가장자리·모서리를 끌어 크기를 바꾼다. 끄는 변만 움직이고 맞은편 변은 제자리다 — 왼쪽·위 변을 끌면 위치가 함께
 * 바뀐다. 크기는 최소·최대 사이, 끄는 변은 뷰포트 여백 안에 멈춘다.
 */
export const resizeRect = (
  start: WindowRect,
  edges: ResizeEdges,
  dx: number,
  dy: number,
  viewport: Viewport,
  limits: WindowLimits,
): WindowRect => {
  const bounds = sizeBounds(viewport, limits);
  let { x, y, width, height } = start;
  const right = start.x + start.width;
  const bottom = start.y + start.height;
  if (edges.right)
    width = clamp(
      start.width + dx,
      bounds.minWidth,
      Math.min(bounds.maxWidth, viewport.width - WINDOW_MARGIN - start.x),
    );
  if (edges.left) {
    width = clamp(start.width - dx, bounds.minWidth, Math.min(bounds.maxWidth, right - WINDOW_MARGIN));
    x = right - width;
  }
  if (edges.bottom)
    height = clamp(
      start.height + dy,
      bounds.minHeight,
      Math.min(bounds.maxHeight, viewport.height - WINDOW_MARGIN - start.y),
    );
  if (edges.top) {
    height = clamp(
      start.height - dy,
      bounds.minHeight,
      Math.min(bounds.maxHeight, bottom - WINDOW_MARGIN),
    );
    y = bottom - height;
  }
  return fitRect({ x, y, width, height }, viewport, limits);
};

const PRESET_SCALE: Record<WindowSizePreset, number> = { small: 0, medium: 0.5, large: 1 };

/**
 * 크기 프리셋(작게·보통·크게): 최소와 최대 사이를 나눈 크기다. 오른쪽·아래 변을 제자리에 두고 줄이거나 늘린다 —
 * 우하단에 붙어 있는 창이 프리셋을 바꿔도 같은 모서리에 남는다.
 */
export const presetRect = (
  start: WindowRect,
  preset: WindowSizePreset,
  viewport: Viewport,
  limits: WindowLimits,
): WindowRect => {
  const bounds = sizeBounds(viewport, limits);
  const scale = PRESET_SCALE[preset];
  const width = Math.round(bounds.minWidth + (bounds.maxWidth - bounds.minWidth) * scale);
  const height = Math.round(bounds.minHeight + (bounds.maxHeight - bounds.minHeight) * scale);
  return fitRect(
    {
      x: start.x + start.width - width,
      y: start.y + start.height - height,
      width,
      height,
    },
    viewport,
    limits,
  );
};

/** 처음 여는 창: 주어진 크기로 우하단에 붙인다. */
export const initialRect = (
  size: { width: number; height: number },
  viewport: Viewport,
  limits: WindowLimits,
): WindowRect =>
  fitRect(
    {
      x: viewport.width - WINDOW_MARGIN - size.width,
      y: viewport.height - WINDOW_MARGIN - size.height,
      ...size,
    },
    viewport,
    limits,
  );

/** 키보드로 옮기는 한 걸음(화살표). 범위 밖이면 멈춘다. */
export const nudgeRect = (
  start: WindowRect,
  key: "ArrowLeft" | "ArrowRight" | "ArrowUp" | "ArrowDown",
  step: number,
  viewport: Viewport,
  limits: WindowLimits,
): WindowRect =>
  moveRect(
    start,
    key === "ArrowLeft" ? -step : key === "ArrowRight" ? step : 0,
    key === "ArrowUp" ? -step : key === "ArrowDown" ? step : 0,
    viewport,
    limits,
  );

/** 보관함에서 읽은 값이 창 위치·크기인가. 모양이 다르면(옛 판·손상) 버리고 기본 자리를 쓴다. */
export const parseWindowRect = (value: unknown): WindowRect | null => {
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  const numbers = ["x", "y", "width", "height"].map((key) => record[key]);
  if (!numbers.every((number) => typeof number === "number" && Number.isFinite(number)))
    return null;
  const [x, y, width, height] = numbers as number[];
  return width! > 0 && height! > 0 ? { x: x!, y: y!, width: width!, height: height! } : null;
};

/**
 * 이 폭 이하에서는 떠 있는 창의 기본 크기가 뷰포트를 대부분 덮는다. 그런 화면에서는 창을 하단 시트(폭 100%,
 * 높이 상한)로 보이고, 열림 기록이 없으면 최소화한 채 시작한다.
 */
export const SHEET_MAX_VIEWPORT_WIDTH = 767;
/** 하단 시트 높이 상한(뷰포트 높이에 대한 비율). */
const SHEET_HEIGHT_RATIO = 0.75;

export const isSheetViewport = (viewport: Viewport): boolean =>
  viewport.width <= SHEET_MAX_VIEWPORT_WIDTH;

/** 하단 시트: 폭 100%, 높이는 상한(뷰포트의 75%·창 최대 높이) 안에서, 아래에 붙는다. */
export const sheetRect = (viewport: Viewport, limits: WindowLimits): WindowRect => {
  const height = Math.max(
    0,
    Math.min(limits.maxHeight, Math.round(viewport.height * SHEET_HEIGHT_RATIO)),
  );
  return { x: 0, y: viewport.height - height, width: viewport.width, height };
};
