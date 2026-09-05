import { expect, test, type Page } from "@playwright/test";

const EDITOR_CHUNK = /\/assets\/code-editor-view-[^/]+\.js(?:\?.*)?$/u;
const REQUIRED_BACKEND_RESOURCES = [
  "/api/v1/strategy-documents/schema",
  "/api/v1/strategy-documents/contract",
  "/api/v1/equity/catalog",
  "/api/v1/factors/catalog",
] as const;
type RequiredBackendResource = (typeof REQUIRED_BACKEND_RESOURCES)[number];

const isRequiredBackendResource = (
  path: string,
): path is RequiredBackendResource =>
  (REQUIRED_BACKEND_RESOURCES as readonly string[]).includes(path);

const openWorkbench = async (page: Page) => {
  const editorChunks: string[] = [];
  const workerUrls: string[] = [];
  const backendResponses = new Map<RequiredBackendResource, number[]>();
  const failedBackendRequests: RequiredBackendResource[] = [];
  page.on("worker", (worker) => {
    workerUrls.push(worker.url());
  });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (EDITOR_CHUNK.test(url.pathname)) editorChunks.push(response.url());
    const backendPath = REQUIRED_BACKEND_RESOURCES.find(
      (path) => path === url.pathname,
    );
    if (backendPath !== undefined) {
      const statuses = backendResponses.get(backendPath) ?? [];
      statuses.push(response.status());
      backendResponses.set(backendPath, statuses);
    }
  });
  page.on("requestfailed", (request) => {
    const path = new URL(request.url()).pathname;
    if (isRequiredBackendResource(path)) failedBackendRequests.push(path);
  });

  const navigation = await page.goto("/research/strategies/new");
  expect(navigation?.ok()).toBe(true);
  await expect(page.getByRole("textbox", { name: "편집기" })).toBeVisible();
  await expect(
    page.getByText("서버 초안 동기화됨", { exact: true }),
  ).toBeVisible();
  await expect.poll(() => editorChunks.length).toBe(1);
  await expect
    .poll(() =>
      REQUIRED_BACKEND_RESOURCES.every(
        (path) => backendResponses.get(path)?.includes(200) === true,
      ),
    )
    .toBe(true);
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  expect(editorChunks).toHaveLength(1);
  expect(failedBackendRequests).toEqual([]);
  for (const path of REQUIRED_BACKEND_RESOURCES) {
    const statuses = backendResponses.get(path);
    expect(statuses, `${path} response statuses`).toBeDefined();
    expect(statuses?.every((status) => status === 200)).toBe(true);
  }

  return {
    editorChunks,
    workerUrls,
    backendResponses,
    failedBackendRequests,
  };
};

test("direct entry loads the lazy worker-free editor from the real backend", async ({
  page,
  request,
}) => {
  const health = await request.get("http://localhost:8000/api/v1/health");
  expect(health.ok()).toBe(true);
  await expect(health.json()).resolves.toEqual({ status: "ok" });

  const observed = await openWorkbench(page);
  expect(observed.editorChunks[0]).toMatch(EDITOR_CHUNK);
  expect([...observed.backendResponses.keys()].sort()).toEqual(
    [...REQUIRED_BACKEND_RESOURCES].sort(),
  );
  expect(observed.workerUrls).toEqual([]);
  expect(observed.failedBackendRequests).toEqual([]);
  expect(page.workers()).toHaveLength(0);
  await expect
    .poll(() =>
      page.evaluate(() => ({
        sans: document.fonts.check('14px "Noto Sans KR Variable"', "전략"),
        mono: document.fonts.check(
          '14px "JetBrains Mono Variable"',
          "StrategySpec",
        ),
      })),
    )
    .toEqual({ sans: true, mono: true });
  await expect(page).toHaveURL(/\/research\/strategies\/new\?draft=/u);
  await expect(page.locator(".code-editor-fallback")).toHaveCount(0);
});

test("matches the professional workbench viewport and theme baseline", async ({
  page,
}, testInfo) => {
  await openWorkbench(page);
  const expectedTheme = testInfo.project.name.endsWith("-dark")
    ? "dark"
    : "light";
  const viewport = testInfo.project.use.viewport;
  expect(viewport).toBeDefined();
  expect(page.viewportSize()).toEqual(viewport);
  await expect
    .poll(() => page.evaluate(() => document.documentElement.dataset.theme))
    .toBe(expectedTheme);
  await page.mouse.move(0, 0);

  await expect(page).toHaveScreenshot("strategy-workbench.png");
});
