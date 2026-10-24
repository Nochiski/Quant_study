import { describe, expect, it } from "vitest";

import {
  WINDOW_MARGIN,
  fitRect,
  initialRect,
  moveRect,
  nudgeRect,
  parseWindowRect,
  presetRect,
  resizeRect,
  isSheetViewport,
  sheetRect,
} from "../floating-geometry";

const VIEWPORT = { width: 1280, height: 800 };
const LIMITS = { minWidth: 320, minHeight: 360, maxWidth: 720, maxHeight: 900 };
const RECT = { x: 800, y: 200, width: 400, height: 500 };

describe("floating window geometry", () => {
  it("처음 창은 우하단 여백에 붙는다", () => {
    expect(initialRect({ width: 400, height: 500 }, VIEWPORT, LIMITS)).toEqual({
      x: 1280 - WINDOW_MARGIN - 400,
      y: 800 - WINDOW_MARGIN - 500,
      width: 400,
      height: 500,
    });
  });

  it("옮겨도 뷰포트 밖으로 나가지 않는다", () => {
    expect(moveRect(RECT, 10_000, 10_000, VIEWPORT, LIMITS)).toMatchObject({
      x: 1280 - WINDOW_MARGIN - 400,
      y: 800 - WINDOW_MARGIN - 500,
    });
    expect(moveRect(RECT, -10_000, -10_000, VIEWPORT, LIMITS)).toMatchObject({
      x: WINDOW_MARGIN,
      y: WINDOW_MARGIN,
    });
  });

  it("오른쪽·아래 변을 끌면 크기만 바뀌고 최소·최대와 뷰포트 여백에서 멈춘다", () => {
    expect(resizeRect(RECT, { right: true }, 50, 0, VIEWPORT, LIMITS)).toEqual({ ...RECT, width: 450 });
    // 오른쪽 여백에서 멈춘다(800 + w ≤ 1272).
    expect(resizeRect(RECT, { right: true }, 1_000, 0, VIEWPORT, LIMITS).width).toBe(472);
    expect(resizeRect(RECT, { right: true }, -1_000, 0, VIEWPORT, LIMITS).width).toBe(320);
    expect(resizeRect(RECT, { bottom: true }, 0, 1_000, VIEWPORT, LIMITS).height).toBe(800 - 8 - 200);
  });

  it("왼쪽·위 변을 끌면 맞은편 변은 제자리이고 위치가 함께 바뀐다", () => {
    const wider = resizeRect(RECT, { left: true, top: true }, -100, -50, VIEWPORT, LIMITS);
    expect(wider).toEqual({ x: 700, y: 150, width: 500, height: 550 });
    // 최대 폭(720)에서 멈추고 오른쪽 변(1200)은 그대로다.
    const capped = resizeRect(RECT, { left: true }, -1_000, 0, VIEWPORT, LIMITS);
    expect(capped.width).toBe(720);
    expect(capped.x + capped.width).toBe(1200);
    // 최소 폭에서 멈춘다.
    const narrowest = resizeRect(RECT, { left: true }, 1_000, 0, VIEWPORT, LIMITS);
    expect(narrowest).toMatchObject({ width: 320, x: 1200 - 320 });
  });

  it("뷰포트가 줄면 창 크기와 위치를 안으로 보정하고, 최소보다 작은 뷰포트에서는 뷰포트가 이긴다", () => {
    expect(fitRect(RECT, { width: 900, height: 600 }, LIMITS)).toEqual({
      x: 900 - 8 - 400,
      y: 600 - 8 - 500,
      width: 400,
      height: 500,
    });
    expect(fitRect(RECT, { width: 300, height: 300 }, LIMITS)).toEqual({
      x: 8,
      y: 8,
      width: 284,
      height: 284,
    });
  });

  it("크기 프리셋은 최소~최대 사이 크기이고 오른쪽·아래 변을 유지한다", () => {
    const small = presetRect(RECT, "small", VIEWPORT, LIMITS);
    expect(small).toMatchObject({ width: 320, height: 360, x: 1200 - 320, y: 700 - 360 });
    const large = presetRect(RECT, "large", VIEWPORT, LIMITS);
    expect(large.width).toBe(720);
    expect(large.height).toBe(800 - 2 * WINDOW_MARGIN);
  });

  it("화살표 한 걸음씩 옮기고 경계에서 멈춘다", () => {
    expect(nudgeRect(RECT, "ArrowLeft", 16, VIEWPORT, LIMITS).x).toBe(784);
    expect(nudgeRect({ ...RECT, y: 8 }, "ArrowUp", 16, VIEWPORT, LIMITS).y).toBe(8);
  });

  it("저장값은 숫자 네 개가 맞을 때만 쓴다", () => {
    expect(parseWindowRect(RECT)).toEqual(RECT);
    expect(parseWindowRect({ ...RECT, width: "400" })).toBeNull();
    expect(parseWindowRect({ ...RECT, height: 0 })).toBeNull();
    expect(parseWindowRect(null)).toBeNull();
  });

  it("좁은 화면은 하단 시트: 폭 100%, 높이는 뷰포트 75%와 최대 높이 안에서 아래에 붙는다", () => {
    expect(isSheetViewport({ width: 390, height: 844 })).toBe(true);
    expect(isSheetViewport({ width: 768, height: 1024 })).toBe(false);
    expect(sheetRect({ width: 390, height: 844 }, LIMITS)).toEqual({
      x: 0,
      y: 844 - 633,
      width: 390,
      height: 633,
    });
    expect(sheetRect({ width: 600, height: 2000 }, LIMITS).height).toBe(900);
  });
});
