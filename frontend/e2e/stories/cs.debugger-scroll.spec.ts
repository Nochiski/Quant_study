import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import { writeFile } from "node:fs/promises";

import { GOLDEN, fillRunEnvironment, openEditor, replaceSource } from "../workbench-helpers";

const centerIsVisible = (target: Locator) => target.evaluate((element) => {
  const box = element.getBoundingClientRect();
  const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
  return hit !== null && element.contains(hit);
});

const scenarios = [
  { name: "연결 실패 기본 높이", width: 1440, failed: true, minimum: false },
  { name: "기본 높이", width: 1440, failed: false, minimum: false },
  { name: "최소 높이", width: 1440, failed: false, minimum: true },
  { name: "좁은 서랍", width: 360, failed: false, minimum: false },
  { name: "연결 실패 좁은 서랍", width: 360, failed: true, minimum: false },
];

const checkScroll = async (page: Page, testInfo: TestInfo, scenario: typeof scenarios[number]) => {
  await page.setViewportSize({ width: scenario.width, height: 900 });
  if (scenario.failed) {
    await page.route("**/api/v1/**", (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "연결할 수 없습니다" }) }));
    await page.goto("/research/strategies/new");
    await expect(page.getByText(/검증 서버에 연결할 수 없어/).first()).toBeVisible();
    await page.waitForLoadState("networkidle");
    await page.evaluate(() => document.fonts.ready);
  } else {
    await openEditor(page, "/research/strategies/new");
    await replaceSource(page, GOLDEN);
    await fillRunEnvironment(page);
  }
  const panel = page.getByRole("region", { name: "중간 결과", exact: true });
  if (scenario.width < 1280) {
    await page.getByRole("button", { name: "중간 결과", exact: true }).click();
  }
  await expect(panel).toBeVisible();
  const collapse = panel.getByRole("button", { name: "중간 결과 접기", exact: true });
  const hint = panel.getByText("필요하면 안에서 스크롤하세요 · 방향키 지원", { exact: true });
  await expect(hint).toBeVisible();
  if (scenario.minimum) {
    await page.getByRole("separator", { name: "중간 결과 크기 조절" }).press("Home");
  }
  await panel.scrollIntoViewIfNeeded();
  const viewport = panel.getByRole("region", { name: "추적 범위와 결과 스크롤 영역" });
  const form = panel.getByRole("form", { name: "전략 추적 범위" });
  const run = form.getByRole("button", { name: "추적 실행", exact: true });
  const factor = form.getByRole("combobox", { name: "팩터", exact: true });
  const node = form.getByRole("combobox", { name: "노드", exact: true });
  if (scenario.failed) await expect(run).toBeDisabled();
  else {
    await form.getByRole("textbox", { name: "종목 ID", exact: true }).fill("sec-005930-1");
    await node.selectOption("mom_252");
    await expect(run).toBeEnabled();
  }
  const measure = () => viewport.evaluate((element) => {
    const box = element.getBoundingClientRect();
    const thumb = getComputedStyle(element, "::-webkit-scrollbar-thumb");
    return { height: box.height, clientHeight: element.clientHeight, scrollHeight: element.scrollHeight, scrollTop: element.scrollTop, scrollbarWidth: element.getBoundingClientRect().width - element.clientWidth, thumbColor: thumb.backgroundColor };
  });
  await viewport.focus();
  await viewport.press("Home");
  await expect.poll(async () => (await measure()).scrollTop).toBe(0);
  await panel.evaluate((element) => element.scrollIntoView({ block: "center" }));
  const panelGeometry = await panel.boundingBox();
  expect(panelGeometry).not.toBeNull();
  if (scenario.width >= 1280) {
    expect(panelGeometry!.height).toBe(scenario.minimum ? 120 : 220);
  } else {
    expect(panelGeometry!.height).toBeLessThanOrEqual(450);
  }
  const before = await measure();
  expect(before.scrollHeight).toBeGreaterThan(before.clientHeight);
  expect(before.scrollbarWidth).toBeGreaterThanOrEqual(8);
  expect(before.thumbColor).not.toBe("rgba(0, 0, 0, 0)");
  await page.screenshot({ path: testInfo.outputPath(`${scenario.name}-initial.png`) });
  // 폼 끝을 보기 위한 실제 휠 입력이다. 마지막 결과까지 지나치지 않고 실행 버튼 위치만큼 이동한다.
  const viewportBox = await viewport.boundingBox();
  const runBox = await run.boundingBox();
  if (viewportBox === null || runBox === null) throw new Error("추적 영역의 위치를 읽을 수 없습니다");
  await page.mouse.move(viewportBox.x + viewportBox.width - 24, viewportBox.y + viewportBox.height / 2);
  await page.mouse.wheel(0, Math.max(0, runBox.y + runBox.height / 2 - viewportBox.y - viewportBox.height / 2));
  await expect.poll(async () => (await measure()).scrollTop).toBeGreaterThan(before.scrollTop);
  await expect.poll(() => centerIsVisible(run)).toBe(true);
  const afterWheel = await measure();
  expect(afterWheel.scrollTop).toBeGreaterThan(before.scrollTop);
  await expect.poll(() => centerIsVisible(factor)).toBe(true);
  await expect.poll(() => centerIsVisible(node)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath(`${scenario.name}-form-end-after-wheel.png`) });
  // 접기 다음 Tab으로 내용에 들어간다. disabled 입력에 초점을 강제로 잡지 않는다.
  await collapse.focus();
  await collapse.press("Tab");
  await expect(viewport).toBeFocused();
  await viewport.press("Home");
  await expect.poll(async () => (await measure()).scrollTop).toBe(0);
  await viewport.press("ArrowDown");
  await expect.poll(async () => (await measure()).scrollTop).toBeGreaterThan(0);
  if (scenario.failed) {
    await viewport.press("Tab");
    const firstTab = panel.getByRole("tab", { name: "연결 추적", exact: true });
    await expect(firstTab).toBeFocused();
    await expect.poll(() => centerIsVisible(firstTab)).toBe(true);
  } else {
    await viewport.focus();
    for (let count = 0; count < 24 && !(await run.evaluate((element) => element === document.activeElement)); count += 1) {
      await page.keyboard.press("Tab");
    }
    await expect(run).toBeFocused();
    await expect.poll(() => centerIsVisible(run)).toBe(true);
    await run.press("Tab");
    await expect(panel.getByRole("tab", { name: "연결 추적", exact: true })).toBeFocused();
  }
  await expect.poll(() => centerIsVisible(collapse)).toBe(true);
  await expect(hint).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath(`${scenario.name}-keyboard-results.png`) });
  const geometryPath = testInfo.outputPath(`${scenario.name}-geometry.json`);
  await writeFile(geometryPath, JSON.stringify({ scenario, panel: panelGeometry, before, afterWheel, final: await measure() }, null, 2));
  await testInfo.attach(`${scenario.name}-debugger-scroll-geometry`, { path: geometryPath, contentType: "application/json" });
  await collapse.click();
  await expect(panel).toBeHidden();
};

test("US-CS-03 기본·최소 높이와 좁은 서랍에서 정상·연결 실패 폼을 휠·키보드로 탐색한다", { tag: ["@story", "@US-CS-03"] }, async ({ browser }, testInfo) => {
  for (const scenario of scenarios) {
    await test.step(scenario.name, async () => {
      const context = await browser.newContext({ baseURL: testInfo.project.use.baseURL, viewport: { width: scenario.width, height: 900 }, locale: "ko-KR" });
      const page = await context.newPage();
      try {
        await checkScroll(page, testInfo, scenario);
      } finally {
        await context.close();
      }
    });
  }
});
