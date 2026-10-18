import { expect, type Locator, type Page } from "@playwright/test";

/** 패널 경계뿐 아니라 긴 내용과 마지막 행동까지 실제 스크롤·hit 위치로 확인한다. */
export const expectResearchMenuReadable = async (
  page: Page,
  summary: Locator,
) => {
  const panel = summary.locator("..").locator(":scope > div");
  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();
  const bounds = await panel.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport!.width);
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport!.height);
  // 데스크톱은 본문 header 기준, 모바일은 기존 viewport 고정 패널이므로 경계 소유자가 다르다.
  if (viewport!.width >= 768) {
    const main = await page.getByRole("main").boundingBox();
    expect(main).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(main!.x);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(main!.x + main!.width);
  }
  const dimensions = await panel.evaluate((element) => ({
    scrollHeight: element.scrollHeight,
    clientHeight: element.clientHeight,
    scrollWidth: element.scrollWidth,
    clientWidth: element.clientWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(
    dimensions.clientWidth + 1,
  );
  if (dimensions.scrollHeight > dimensions.clientHeight) {
    await panel.hover();
    await page.mouse.wheel(0, dimensions.scrollHeight);
    await expect
      .poll(() => panel.evaluate((element) => element.scrollTop))
      .toBeGreaterThan(0);
  }
  for (const content of await panel.locator("p, a, button").all()) {
    await content.scrollIntoViewIfNeeded();
    await expect
      .poll(() =>
        content.evaluate((element) => {
          const box = element.getBoundingClientRect();
          const hit = document.elementFromPoint(
            box.x + box.width / 2,
            box.y + box.height / 2,
          );
          return hit !== null && element.contains(hit);
        }),
      )
      .toBe(true);
    // 각 줄의 글자 양끝을 검사해 중심점은 보이지만 문장 앞이 sidebar에 가려지는 경우를 잡는다.
    await expect
      .poll(() =>
        content.evaluate((element) => {
          const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
          let node: Node | null;
          let lines = 0;
          while ((node = walker.nextNode()) !== null) {
            if (node.textContent?.trim() === "") continue;
            const range = document.createRange();
            range.selectNodeContents(node);
            for (const box of range.getClientRects()) {
              if (box.width === 0 || box.height === 0) continue;
              lines++;
              const inset = Math.min(2, box.width / 2);
              for (const x of [box.left + inset, box.right - inset]) {
                const hit = document.elementFromPoint(x, box.y + box.height / 2);
                if (hit === null || !element.contains(hit)) return false;
              }
            }
          }
          return lines > 0;
        }),
      )
      .toBe(true);
  }
};
