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
  }
};
