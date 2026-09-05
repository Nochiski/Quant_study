import { expect, test, type Browser, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const BACKEND = "http://localhost:8000";
const ownDirectory = dirname(fileURLToPath(import.meta.url));
const GOLDEN = readFileSync(
  resolve(
    ownDirectory,
    "../../backend/tests/fixtures/strategy_documents/quality_momentum.yaml",
  ),
  "utf8",
);

const editor = (page: Page) =>
  page.getByRole("textbox", { name: "편집기", exact: true });
const save = (page: Page) =>
  page.getByRole("button", { name: "리비전 저장", exact: true });
const validate = (page: Page) =>
  page.getByRole("button", { name: "검증", exact: true });
const backtest = (page: Page) =>
  page.getByRole("button", { name: "백테스트", exact: true });

const openEditor = async (page: Page, url: string) => {
  const response = await page.goto(url);
  expect(response?.ok()).toBe(true);
  await expect(editor(page)).toBeVisible();
};

const replaceSource = async (page: Page, source: string) => {
  await editor(page).fill(source);
};

const expectPhase = async (page: Page, phase: string) => {
  await expect(page.locator(".source-editor__status")).toContainText(phase);
};

const currentSource = async (page: Page) => {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: "http://localhost:5173",
  });
  await editor(page).click();
  await editor(page).press("Control+A");
  await editor(page).press("Control+C");
  return page.evaluate(() => navigator.clipboard.readText());
};

const strategyIdentity = (page: Page) => {
  const match = new URL(page.url()).pathname.match(
    /^\/research\/strategies\/([^/]+)\/revisions\/(\d+)$/u,
  );
  if (match === null)
    throw new Error(`Not on a strategy revision: ${page.url()}`);
  return { strategyId: match[1]!, revision: Number(match[2]) };
};

const saveAndWaitForRevision = async (page: Page, revision: number) => {
  await expect(save(page)).toBeEnabled();
  await save(page).click();
  await expect(page).toHaveURL(
    new RegExp(
      `/research/strategies/[^/]+/revisions/${revision}(?:\\?.*)?$`,
      "u",
    ),
  );
  await expectPhase(page, "저장됨");
};

const openConflictingEditor = async (browser: Browser, revisionUrl: string) => {
  const context = await browser.newContext({
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
    viewport: { width: 1440, height: 900 },
    colorScheme: "light",
  });
  const page = await context.newPage();
  await openEditor(page, revisionUrl);
  await expectPhase(page, "저장됨");
  return { context, page };
};

test.describe("professional YAML workflow", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(180_000);

  test("migrates old bookmarks without mounting the retired editor", async ({
    page,
  }) => {
    await openEditor(page, "/?step=portfolio&run=bt-old");
    await expect(page).toHaveURL(
      /\/research\/strategies\/new\?draft=draft-[a-f0-9]{32}$/u,
    );
    expect(page.url()).not.toContain("step=");
    expect(page.url()).not.toContain("run=");
    await expect(page.getByText("Quick Builder", { exact: true })).toHaveCount(
      0,
    );

    await openEditor(page, "/legacy/builder?step=risk&run=bt-old");
    await expect(page).toHaveURL(
      /\/research\/strategies\/new\?draft=draft-[a-f0-9]{32}$/u,
    );
    await expect(page.getByRole("link", { name: "기존 편집기" })).toHaveCount(
      0,
    );
  });

  test("creates, recovers, validates, versions, traces and backtests", async ({
    browser,
    page,
  }) => {
    const titleV1 = "P6-06 E2E 모멘텀";
    const sourceV1 = GOLDEN.replace("퀄리티 모멘텀", titleV1);
    await openEditor(page, "/research/strategies/new");

    const draftSaved = page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        new URL(response.url()).pathname.startsWith(
          "/api/v1/strategy-drafts/",
        ) &&
        response.status() === 200,
    );
    await replaceSource(page, sourceV1);
    await expectPhase(page, "검증 통과");
    await draftSaved;
    await expect
      .poll(() =>
        page.evaluate(() =>
          localStorage.getItem("strategy-workbench.draft.new"),
        ),
      )
      .not.toBeNull();
    const draftUrl = new URL(page.url());

    await page.reload();
    await expect(editor(page)).toBeVisible();
    const localRecovery = page.getByRole("region", { name: "복구본" });
    await expect(localRecovery).toBeVisible();
    await expect(
      page.getByRole("region", { name: "복구할 서버 초안" }),
    ).toBeVisible();
    await localRecovery
      .getByRole("button", { name: "복구본 불러오기" })
      .click();
    await expect.poll(() => currentSource(page)).toBe(sourceV1);
    await page
      .getByRole("region", { name: "복구할 서버 초안" })
      .getByRole("button", { name: "서버 초안 적용" })
      .click();
    await expect.poll(() => currentSource(page)).toBe(sourceV1);
    const recoveredUrl = new URL(page.url());
    expect(recoveredUrl.pathname).toBe(draftUrl.pathname);
    expect(recoveredUrl.searchParams.get("draft")).toBe(
      draftUrl.searchParams.get("draft"),
    );

    const invalid = sourceV1.replace("max_name_weight", "max_name_wieght");
    await replaceSource(page, invalid);
    await expectPhase(page, "구조 오류");
    await expect(save(page)).toBeDisabled();
    await expect(backtest(page)).toBeDisabled();
    await expect(page.getByRole("region", { name: "문제" })).toContainText(
      "/risk/max_name_wieght",
    );

    await replaceSource(page, sourceV1);
    await expectPhase(page, "검증 통과");
    await expect(validate(page)).toBeEnabled();
    const compiled = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname ===
          "/api/v1/strategy-documents/compile" &&
        response.status() === 200,
    );
    await validate(page).click();
    await compiled;
    await expectPhase(page, "검증 통과");

    await saveAndWaitForRevision(page, 1);
    const { strategyId } = strategyIdentity(page);
    await page.reload();
    await expect(editor(page)).toBeVisible();
    await expectPhase(page, "저장됨");
    await expect.poll(() => currentSource(page)).toBe(sourceV1);

    const titleV2 = `${titleV1} v2`;
    const sourceV2 = sourceV1.replace(titleV1, titleV2);
    await replaceSource(page, sourceV2);
    await expectPhase(page, "검증 통과");
    await saveAndWaitForRevision(page, 2);
    const v2Url = `${new URL(page.url()).pathname}`;

    await page.getByRole("tab", { name: "Diff", exact: true }).click();
    const diff = page.getByRole("region", { name: "StrategySpec Diff" });
    await expect(diff).toBeVisible();
    await expect(diff.getByText("저장 revision 비교")).toBeVisible();
    await expect(diff.getByLabel("기준 revision")).toHaveValue("1");
    await expect(diff.getByLabel("대상 revision")).toHaveValue("2");
    await expect(diff).toContainText("/title");

    await page.getByRole("tab", { name: "YAML", exact: true }).click();
    const conflicting = await openConflictingEditor(browser, v2Url);
    const sourceV3 = sourceV2.replace(titleV2, `${titleV1} v3`);
    await replaceSource(page, sourceV3);
    await expectPhase(page, "검증 통과");
    await saveAndWaitForRevision(page, 3);

    const finalTitle = `${titleV1} conflict v4`;
    const finalSource = sourceV2.replace(titleV2, finalTitle);
    await replaceSource(conflicting.page, finalSource);
    await expectPhase(conflicting.page, "검증 통과");
    await expect(save(conflicting.page)).toBeEnabled();
    await save(conflicting.page).click();
    const conflict = conflicting.page.getByRole("region", {
      name: "리비전 충돌",
    });
    await expect(conflict).toBeVisible();
    await expect(conflict).toContainText("서버 최신 v3 · 현재 기준 v2");
    await conflict
      .getByRole("button", { name: "현재 전체 문서로 v4 생성" })
      .click();
    await expect(conflicting.page).toHaveURL(
      new RegExp(
        `/research/strategies/${strategyId}/revisions/4(?:\\?.*)?$`,
        "u",
      ),
    );
    await expectPhase(conflicting.page, "저장됨");

    const workflow = conflicting.page;
    await workflow.getByLabel("종목 ID").fill("sec-005930-1, sec-000660-1");
    await expect(
      workflow.getByRole("button", { name: "추적 실행" }),
    ).toBeEnabled();
    await workflow.getByRole("button", { name: "추적 실행" }).click();
    await expect(workflow.locator('[aria-label="추적 재현 정보"]')).toBeVisible(
      { timeout: 60_000 },
    );
    const linked = workflow.getByRole("tabpanel", { name: "연결 추적" });
    await expect(linked).toContainText("sec-005930-1");
    await expect(linked).toContainText("6 제약 전 목표");
    await expect(linked).toContainText("7 위험 제약 후");

    await workflow.getByRole("tab", { name: "TargetTape" }).click();
    const target = workflow.getByRole("region", {
      name: "TargetTape 후보와 선택 노드 결과",
    });
    await expect(target).toContainText("sec-000660-1");
    await expect(target).toContainText("제외 사유");
    await workflow.getByRole("tab", { name: "원시 데이터" }).click();
    await expect(
      workflow.getByRole("region", {
        name: "원시 필드 값, 공개일과 데이터 상태",
      }),
    ).toContainText("price.close");
    await workflow.getByRole("tab", { name: "선택 노드" }).click();
    await expect(
      workflow.getByRole("region", {
        name: "선택한 FactorGraph 노드의 실제 계산 결과",
      }),
    ).toContainText("mom_252");
    await workflow.getByRole("tab", { name: "실행 계획" }).click();
    await expect(
      workflow.getByText("실행 계획", { exact: true }),
    ).toBeVisible();

    await expect(backtest(workflow)).toBeEnabled();
    await backtest(workflow).click();
    await expect(workflow).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    const runId = new URL(workflow.url()).pathname.split("/").at(-1)!;
    await expect(workflow.locator(".page-header .ui-badge")).toContainText(
      "completed",
      {
        useInnerText: true,
        timeout: 120_000,
      },
    );
    await expect(
      workflow.getByRole("heading", { name: "백테스트 결과" }),
    ).toBeVisible({ timeout: 120_000 });

    await workflow.getByRole("link", { name: "백테스트" }).click();
    await expect(
      workflow.getByRole("heading", { name: "백테스트 이력" }),
    ).toBeVisible();
    const runRow = workflow.getByRole("row").filter({ hasText: runId });
    await expect(runRow).toContainText("completed");
    await expect(runRow).toContainText(`${strategyId} · v4`);

    await workflow.getByRole("link", { name: "전략" }).click();
    await expect(
      workflow.getByRole("heading", { name: "전략 이력" }),
    ).toBeVisible();
    const strategyRow = workflow
      .getByRole("row")
      .filter({ hasText: strategyId })
      .first();
    await expect(strategyRow).toContainText(finalTitle);
    await expect(strategyRow).toContainText("v4");
    await workflow
      .getByRole("button", {
        name: `Revision 펼치기: ${finalTitle} (${strategyId})`,
      })
      .click();
    const revisions = workflow.getByRole("region", {
      name: `저장 revision 목록: ${finalTitle} (${strategyId})`,
    });
    await expect(revisions).toContainText("v1");
    await expect(revisions).toContainText("v4");
    await expect(revisions.getByRole("link", { name: "Diff" })).toHaveCount(4);

    await conflicting.context.close();
  });

  test("migrates a source-less legacy revision without changing meaning", async ({
    page,
    request,
  }) => {
    const template = await request.get(`${BACKEND}/api/v1/strategies/template`);
    expect(template.ok()).toBe(true);
    const created = await request.post(`${BACKEND}/api/v1/strategies`, {
      data: await template.json(),
    });
    expect(created.status()).toBe(201);
    const saved = (await created.json()) as {
      spec_hash: string;
      spec: { identity: { strategy_id: string } };
    };
    const strategyId = saved.spec.identity.strategy_id;
    const documentResponse = await request.get(
      `${BACKEND}/api/v1/strategies/${strategyId}/revisions/1/document`,
    );
    expect(documentResponse.ok()).toBe(true);
    const generated = (await documentResponse.json()) as {
      generated: boolean;
      origin: string;
      source: string;
      spec_hash: string;
    };
    expect(generated.generated).toBe(true);
    expect(generated.origin).toBe("legacy_json");
    expect(generated.spec_hash).toBe(saved.spec_hash);

    await openEditor(page, `/research/strategies/${strategyId}/revisions/1`);
    await expect(page.getByText("legacy JSON에서 생성된 문서")).toBeVisible();
    const whitespaceOnly = `${generated.source.trimEnd()}\n\n`;
    await replaceSource(page, whitespaceOnly);
    await expectPhase(page, "검증 통과");
    await saveAndWaitForRevision(page, 2);
    const migratedResponse = await request.get(
      `${BACKEND}/api/v1/strategies/${strategyId}/revisions/2/document`,
    );
    expect(migratedResponse.ok()).toBe(true);
    const migrated = (await migratedResponse.json()) as {
      generated: boolean;
      origin: string;
      source: string;
      spec_hash: string;
    };
    expect(migrated.generated).toBe(false);
    expect(migrated.origin).toBe("document");
    expect(migrated.source).toBe(whitespaceOnly);
    expect(migrated.spec_hash).toBe(saved.spec_hash);
  });
});
