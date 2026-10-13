import { expect, test, type Page } from "@playwright/test";
import {
  currentSource,
  GOLDEN,
  openEditor,
  replaceSource,
} from "../workbench-helpers";
import { waitForSettledDocument } from "../editor-helpers";

const graph = (page: Page) =>
  page.getByRole("region", { name: "그래프 편집", exact: true });
const canvas = (page: Page) =>
  graph(page).getByRole("region", { name: "노드 캔버스", exact: true });
const output = (page: Page, index: number) =>
  canvas(page).getByRole("button", {
    name: new RegExp(`^${index}\\..* · 출력$`),
  });
const input = (page: Page) =>
  canvas(page).getByRole("button", { name: /^2\..* · 입력 노드 ·/ });
const layout = (page: Page) =>
  graph(page).getByRole("status", { name: "노드 캔버스", exact: true });

test("노드 캔버스 배선·키보드·취소·배치·YAML undo가 같은 원문을 지킨다", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const workers: string[] = [];
  page.on("worker", (worker) => workers.push(worker.url()));
  await openEditor(page, "/research/strategies/new?view=yaml");
  await replaceSource(page, GOLDEN);
  await waitForSettledDocument(page);
  expect(workers).toEqual([]);
  await page.getByRole("tab", { name: "그래프", exact: true }).click();
  await expect(layout(page)).toHaveAttribute("data-layout-status", "ready");
  expect(workers.some((url) => /elk-worker/.test(url))).toBe(true);
  const firstMs = Number(await layout(page).getAttribute("data-layout-ms"));
  expect(Number.isFinite(firstMs) && firstMs >= 0).toBe(true);

  // 복제로 유효한 세 번째 소스 노드를 만든다. source를 주입해 배선을 완성하지 않는다.
  await canvas(page)
    .getByRole("button", { name: /^1\..* · 복제$/ })
    .click();
  await expect(
    canvas(page).getByRole("button", { name: /^노드 편집:/ }),
  ).toHaveCount(3);
  await waitForSettledDocument(page);
  await expect(layout(page)).toHaveAttribute("data-layout-status", "ready");
  await page.getByRole("tab", { name: "YAML", exact: true }).click();
  const before = await currentSource(page);
  expect(before).toContain("node_id: close_2");
  await page.getByRole("tab", { name: "그래프", exact: true }).click();
  await output(page, 3).dragTo(input(page));
  await waitForSettledDocument(page);
  await page.getByRole("tab", { name: "YAML", exact: true }).click();
  expect(await currentSource(page)).toBe(
    before.replace("input_node_id: close\n", "input_node_id: close_2\n"),
  );
  await page.getByRole("button", { name: "실행 취소", exact: true }).click();
  await waitForSettledDocument(page);
  expect(await currentSource(page)).toBe(before);
  await page.getByRole("tab", { name: "그래프", exact: true }).click();

  // 키보드도 같은 연결 연산을 호출하며 Escape 뒤에는 연결이 남지 않는다.
  await output(page, 3).focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Escape");
  await input(page).focus();
  await page.keyboard.press("Enter");
  await expect(
    graph(page).getByRole("button", { name: "연결 취소", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "YAML", exact: true }).click();
  expect(await currentSource(page)).toBe(before);
  await page.getByRole("tab", { name: "그래프", exact: true }).click();
  await output(page, 3).focus();
  await page.keyboard.press("Enter");
  await input(page).focus();
  await page.keyboard.press("Enter");
  await waitForSettledDocument(page);
  await page.getByRole("tab", { name: "YAML", exact: true }).click();
  expect(await currentSource(page)).toBe(
    before.replace("input_node_id: close\n", "input_node_id: close_2\n"),
  );
  await page.getByRole("button", { name: "실행 취소", exact: true }).click();
  await waitForSettledDocument(page);
  expect(await currentSource(page)).toBe(before);
  await page.getByRole("tab", { name: "그래프", exact: true }).click();

  const first = canvas(page).getByRole("button", { name: /^노드 편집: 1\./ });
  await first.focus();
  await page.keyboard.press("ArrowRight");
  await expect(
    canvas(page).getByRole("button", { name: /^노드 편집: 2\./ }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(
    graph(page).getByRole("group", { name: /선택한 노드/ }),
  ).toBeFocused();
  await expect(
    graph(page).getByRole("group", { name: /선택한 노드/ }),
  ).toBeInViewport();
  const move = canvas(page).getByRole("button", { name: /^1\..* · 이동$/ });
  const own = move.locator("..");
  const initialLeft = await own.evaluate(
    (element) => (element as HTMLElement).style.left,
  );
  await move.focus();
  await page.keyboard.press("Shift+ArrowRight");
  expect(
    await own.evaluate((element) => (element as HTMLElement).style.left),
  ).not.toBe(initialLeft);
  await page.getByRole("tab", { name: "YAML", exact: true }).click();
  expect(await currentSource(page)).toBe(before); // 이동은 source/undo 이력을 쓰지 않는다.
  await page.getByRole("tab", { name: "그래프", exact: true }).click();
  const repeatedMs: number[] = [];
  for (let i = 0; i < 3; i++) {
    await graph(page)
      .getByRole("button", { name: "자동 정렬", exact: true })
      .click();
    await expect(layout(page)).toHaveAttribute("data-layout-status", "ready");
    repeatedMs.push(Number(await layout(page).getAttribute("data-layout-ms")));
  }
  expect(
    repeatedMs.every((value) => Number.isFinite(value) && value >= 0),
  ).toBe(true);
  await test.info().attach("elk-browser-layout", {
    contentType: "application/json",
    body: JSON.stringify({ firstMs, repeatedMs, workers: workers.length }),
  });
  for (const width of [1440, 640, 360]) {
    await page.setViewportSize({ width, height: 1000 });
    await canvas(page).scrollIntoViewIfNeeded();
    await test.info().attach(`node-canvas-${width}`, {
      contentType: "image/png",
      body: await page.screenshot(),
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
});
