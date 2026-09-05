import { expect, test, type Page } from "@playwright/test";

const EDITOR_CHUNK = /\/assets\/code-editor-view-[^/]+\.js(?:\?.*)?$/u;
const FONT_ASSETS = {
  mono: /\/assets\/jetbrains-mono-[^/]+\.woff2$/u,
  sans: /\/assets\/noto-sans-kr-[^/]+\.woff2$/u,
} as const;
type FontAsset = keyof typeof FONT_ASSETS;
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

const fontAssetOf = (path: string): FontAsset | undefined =>
  (Object.entries(FONT_ASSETS) as [FontAsset, RegExp][]).find(([, pattern]) =>
    pattern.test(path),
  )?.[0];

const openWorkbench = async (page: Page) => {
  const editorChunks: string[] = [];
  const workerUrls: string[] = [];
  const backendResponses = new Map<RequiredBackendResource, number[]>();
  const failedBackendRequests: RequiredBackendResource[] = [];
  const fontResponses = new Map<FontAsset, number[]>();
  const failedFontRequests: FontAsset[] = [];
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
    const fontAsset = fontAssetOf(url.pathname);
    if (fontAsset !== undefined) {
      const statuses = fontResponses.get(fontAsset) ?? [];
      statuses.push(response.status());
      fontResponses.set(fontAsset, statuses);
    }
  });
  page.on("requestfailed", (request) => {
    const path = new URL(request.url()).pathname;
    if (isRequiredBackendResource(path)) failedBackendRequests.push(path);
    const fontAsset = fontAssetOf(path);
    if (fontAsset !== undefined) failedFontRequests.push(fontAsset);
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
  const fontEvidence = await page.evaluate(async () => {
    const [sansFaces, monoFaces, missingFaces] = await Promise.all([
      document.fonts.load('14px "Noto Sans KR Variable"', "전략"),
      document.fonts.load('14px "JetBrains Mono Variable"', "StrategySpec"),
      document.fonts.load('14px "Definitely Missing Font 123"', "전략"),
    ]);
    await document.fonts.ready;
    const editor = document.querySelector<HTMLElement>(".cm-content");
    if (editor === null) throw new Error("CodeMirror content is not mounted");
    return {
      bodyFamily: getComputedStyle(document.body).fontFamily,
      editorFamily: getComputedStyle(editor).fontFamily,
      sansLoaded: sansFaces.filter((face) => face.status === "loaded").length,
      monoLoaded: monoFaces.filter((face) => face.status === "loaded").length,
      missingFaces: missingFaces.length,
    };
  });
  await page.waitForLoadState("networkidle");
  expect(editorChunks).toHaveLength(1);
  expect(failedBackendRequests).toEqual([]);
  expect(failedFontRequests).toEqual([]);
  for (const path of REQUIRED_BACKEND_RESOURCES) {
    const statuses = backendResponses.get(path);
    expect(statuses, `${path} response statuses`).toBeDefined();
    expect(statuses?.every((status) => status === 200)).toBe(true);
  }
  expect(fontEvidence.bodyFamily).toContain("Noto Sans KR Variable");
  expect(fontEvidence.editorFamily).toContain("JetBrains Mono Variable");
  expect(fontEvidence.sansLoaded).toBeGreaterThan(0);
  expect(fontEvidence.monoLoaded).toBeGreaterThan(0);
  expect(fontEvidence.missingFaces).toBe(0);
  for (const family of Object.keys(FONT_ASSETS) as FontAsset[]) {
    const statuses = fontResponses.get(family);
    expect(statuses, `${family} font response statuses`).toBeDefined();
    expect(statuses?.every((status) => status === 200)).toBe(true);
  }

  return {
    editorChunks,
    workerUrls,
    backendResponses,
    failedBackendRequests,
    fontResponses,
    failedFontRequests,
    fontEvidence,
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
  expect([...observed.fontResponses.keys()].sort()).toEqual(["mono", "sans"]);
  expect(observed.failedFontRequests).toEqual([]);
  expect(observed.fontEvidence.editorFamily).toContain(
    "JetBrains Mono Variable",
  );
  expect(page.workers()).toHaveLength(0);
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
