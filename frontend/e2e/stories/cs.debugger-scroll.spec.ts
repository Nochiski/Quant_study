import { expect, test, type Locator } from "@playwright/test";
import { writeFile } from "node:fs/promises";

import { GOLDEN, fillRunEnvironment, openEditor, replaceSource } from "../workbench-helpers";

const centerIsVisible = (target: Locator) => target.evaluate((element) => {
  const box = element.getBoundingClientRect();
  const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
  return hit !== null && element.contains(hit);
});

test("US-CS-03 작은 분할 기록과 무관하게 세 화면에서 추적하고 편집으로 복귀한다", { tag: ["@story", "@US-CS-03"] }, async ({ browser }, testInfo) => {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 1280, height: 800 }, { width: 360, height: 800 }]) {
    await test.step(`${viewport.width}px 추적 작업`, async () => {
      const context = await browser.newContext({ baseURL: testInfo.project.use.baseURL, viewport, locale: "ko-KR" });
      const page = await context.newPage();
      try {
    await page.setViewportSize(viewport);
    await page.addInitScript(() => localStorage.setItem("quant-workbench.panel-sizes.v1", JSON.stringify({ version: 1, sizes: { outlineWidth: 240, inspectorWidth: 320, debuggerHeight: 120 } })));
    await openEditor(page, "/research/strategies/new");
    await replaceSource(page, GOLDEN);
    await fillRunEnvironment(page);
    await page.getByRole("button", { name: "중간 결과", exact: true }).click();
    const panel = page.getByRole("region", { name: "중간 결과", exact: true });
    const form = panel.getByRole("form", { name: "전략 추적 범위" });
    const run = form.getByRole("button", { name: "추적 실행", exact: true });
    await form.getByRole("textbox", { name: "종목 ID", exact: true }).fill("sec-005930-1, sec-000660-1, sec-035420-1");
    await form.getByRole("combobox", { name: "팩터", exact: true }).selectOption("momentum");
    await form.getByRole("combobox", { name: "노드", exact: true }).selectOption("mom_252");
    // 폼 끝은 손잡이 확대나 중첩 스크롤 없이 조작한다.
    await expect.poll(() => centerIsVisible(run)).toBe(true);
    await run.click();
    await expect(panel.getByText(/추적 실패/, { exact: true })).toHaveCount(0);
    await expect(panel.getByText(/추적 출처/, { exact: false })).toContainText("2026");
    const linked = panel.getByRole("tabpanel", { name: "연결 추적", exact: true });
    const list = linked.getByRole("list", { name: "연결 추적", exact: true });
    await expect(list.getByRole("listitem").first()).toContainText("mom_252");
    const panelBox = await panel.boundingBox();
    const resultBox = await linked.boundingBox();
    expect(panelBox).not.toBeNull();
    expect(resultBox).not.toBeNull();
    expect(panelBox!.height).toBeGreaterThan(viewport.height * 0.45);
    expect(resultBox!.height).toBeGreaterThan(180);
    expect(resultBox!.y + resultBox!.height).toBeLessThanOrEqual(viewport.height);
    if (viewport.width < 720) {
      await linked.getByRole("combobox", { name: "비교할 종목" }).selectOption("sec-000660-1");
      await expect(list.getByRole("listitem").first()).toContainText("sec-000660-1");
      await expect(list.locator(":scope > article")).toHaveCount(1);
      await list.focus();
      await list.press("End");
      await expect.poll(() => list.evaluate((element) => Math.abs(element.scrollTop - (element.scrollHeight - element.clientHeight)))).toBeLessThanOrEqual(1);
      await list.press("Home");
      await expect.poll(() => list.evaluate((element) => element.scrollTop)).toBe(0);
      await expect.poll(() => list.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    }
    for (const name of ["원시 데이터", "선택 노드", "TargetTape", "연결 추적"]) {
      const tab = panel.getByRole("tab", { name, exact: true });
      await panel.getByRole("tab", { selected: true }).focus();
      for (let count = 0; count < 5 && await tab.getAttribute("aria-selected") !== "true"; count += 1) {
        await panel.getByRole("tab", { selected: true }).press("ArrowRight");
      }
      await expect(tab).toBeFocused();
      await expect.poll(() => centerIsVisible(tab)).toBe(true);
      await tab.click();
      await expect(panel.getByRole("tabpanel", { name, exact: true })).toBeVisible();
    }
    if (viewport.width < 720) await expect(linked.getByRole("combobox", { name: "비교할 종목" })).toHaveValue("sec-000660-1");
    await page.screenshot({ path: testInfo.outputPath(`trace-workspace-${viewport.width}.png`) });
    await writeFile(testInfo.outputPath(`workspace-geometry-${viewport.width}.json`), JSON.stringify({ viewport, panel: panelBox, results: resultBox }, null, 2));
    await page.getByRole("button", { name: "전략 편집", exact: true }).click();
    await expect(panel).toBeHidden();
    await page.getByRole("button", { name: "중간 결과", exact: true }).click();
    await expect(list.getByRole("listitem").first()).toContainText("mom_252");
    const scope = panel.locator("summary").filter({ hasText: "전략 추적 범위" });
    if (!(await form.isVisible())) await scope.click();
    await expect(form.getByRole("textbox", { name: "종목 ID", exact: true })).toHaveValue("sec-005930-1, sec-000660-1, sec-035420-1");
      } finally { await context.close(); }
    });
  }
});


test("US-CS-03 연결 실패 상태도 추적 입력 끝과 복귀 행동을 창 안에서 읽는다", { tag: ["@story", "@US-CS-03"] }, async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await page.route("**/api/v1/**", (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "연결할 수 없습니다" }) }));
  await page.goto("/research/strategies/new");
  await expect(page.getByText(/검증 서버에 연결할 수 없어/).first()).toBeVisible();
  await page.getByRole("button", { name: "중간 결과", exact: true }).click();
  const panel = page.getByRole("region", { name: "중간 결과", exact: true });
  const run = panel.getByRole("button", { name: "추적 실행", exact: true });
  await expect(run).toBeDisabled();
  await expect.poll(() => centerIsVisible(run)).toBe(true);
  const back = panel.getByRole("button", { name: "편집으로 돌아가기", exact: true });
  await expect.poll(() => centerIsVisible(back)).toBe(true);
  await back.focus();
  await back.press("Tab");
  await expect(panel.locator("summary").filter({ hasText: "전략 추적 범위" })).toBeFocused();
  await back.click();
  await expect(panel).toBeHidden();
});
