import { chromium, expect } from "@playwright/test";
import { mkdir, readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDirectory, "../..");
const outputDirectory = resolve(
  repositoryRoot,
  "docs/manual/strategy-workbench/assets",
);
const fixturePath = resolve(
  repositoryRoot,
  "backend/tests/fixtures/strategy_documents/quality_momentum.yaml",
);
const frontendUrl =
  process.env.WORKBENCH_MANUAL_FRONTEND_URL ?? "http://localhost:5173";
const backendUrl =
  process.env.WORKBENCH_MANUAL_BACKEND_URL ?? "http://127.0.0.1:8000";
const headed = process.env.WORKBENCH_MANUAL_HEADED === "1";

const assertReachable = async (url, label) => {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${label} 응답 실패: ${response.status} ${url}`);
  }
};

const capture = async (page, filename, locator) => {
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  await page.mouse.move(1590, 990);
  await page.waitForTimeout(100);
  const target = resolve(outputDirectory, filename);
  const options = {
    path: target,
    animations: "disabled",
    caret: "hide",
  };
  if (locator === undefined) {
    await page.evaluate(() => window.scrollTo({ top: 0, left: 0 }));
    await page.screenshot(options);
  } else {
    await locator.screenshot(options);
  }
  process.stdout.write(`스크린샷 저장: ${target}\n`);
};

await assertReachable(`${backendUrl}/api/v1/health`, "백엔드");
await assertReachable(frontendUrl, "프론트엔드");
await mkdir(outputDirectory, { recursive: true });

const golden = (await readFile(fixturePath, "utf8")).replace(/\r\n?/gu, "\n");
const titleV1 = "사용자 매뉴얼 모멘텀";
const titleV2 = `${titleV1} 개선안`;
const sourceV1 = golden
  .replace('title: "퀄리티 모멘텀"', `title: "${titleV1}"`)
  .replace("selection_count: 20", "selection_count: 2");
const sourceV2 = sourceV1.replace(titleV1, titleV2);

const browser = await chromium.launch({ headless: !headed });
const context = await browser.newContext({
  locale: "ko-KR",
  timezoneId: "Asia/Seoul",
  viewport: { width: 1600, height: 1000 },
  colorScheme: "light",
});
const page = await context.newPage();
const editor = page.getByRole("textbox", { name: "편집기", exact: true });
const documentStatus = page.getByRole("status", { name: "문서 상태" });
const saveButton = page.getByRole("button", {
  name: "리비전 저장",
  exact: true,
});
const backtestButton = page.getByRole("button", {
  name: "백테스트",
  exact: true,
});
const backtestPosts = [];
page.on("request", (request) => {
  if (
    request.method() === "POST" &&
    new URL(request.url()).pathname === "/api/v1/backtests"
  ) {
    backtestPosts.push(request);
  }
});

try {
  await page.goto(`${frontendUrl}/research/strategies/new`);
  await expect(editor).toBeVisible();
  await capture(page, "01-new-strategy.png");

  await editor.fill(sourceV1);
  await expect(documentStatus).toContainText("검증 통과");
  await capture(page, "02-valid-yaml.png");

  const outline = page.getByRole("tree", { name: "StrategySpec 문서 구조" });
  const risk = outline.getByRole("treeitem", { name: "risk", exact: true });
  await risk.focus();
  if ((await risk.getAttribute("aria-expanded")) !== "true") {
    await risk.press("ArrowRight");
  }
  await outline
    .getByRole("treeitem", { name: "max_name_weight", exact: true })
    .click();
  await expect(page.getByText("5%", { exact: true })).toBeVisible();
  await capture(page, "03-contract-inspector.png");

  const invalidSource = sourceV1.replace("max_name_weight", "max_name_wieght");
  await editor.fill(invalidSource);
  await expect(documentStatus).toContainText("구조 오류");
  await expect(page.getByRole("region", { name: "문제" })).toContainText(
    "/risk/max_name_wieght",
  );
  await capture(page, "04-structure-error.png");

  await editor.fill(sourceV1);
  await expect(documentStatus).toContainText("검증 통과");
  await expect(saveButton).toBeEnabled();
  const createdResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/strategy-documents" &&
      response.status() === 201,
  );
  await saveButton.click();
  const createdDocument = await (await createdResponse).json();
  await expect(page).toHaveURL(
    /\/research\/strategies\/[^/]+\/revisions\/1(?:\?.*)?$/u,
  );
  await expect(
    page.locator(
      `.doc-toolbar__identity code[title="${createdDocument.spec_hash}"]`,
    ),
  ).toBeVisible();
  await expect(documentStatus).toContainText("저장됨");
  await expect(page.getByText("전략 구조를 분석하는 중입니다.")).toHaveCount(0);
  const revisionOneUrl = page.url();
  const strategyId = new URL(revisionOneUrl).pathname.split("/")[3];
  if (strategyId === undefined || strategyId === "") {
    throw new Error(`저장된 전략 ID를 찾을 수 없습니다: ${revisionOneUrl}`);
  }
  await capture(page, "05-saved-revision.png");

  await editor.fill(sourceV2);
  await expect(documentStatus).toContainText("검증 통과");
  const revisedResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname ===
        `/api/v1/strategy-documents/${strategyId}/revisions` &&
      response.status() === 201,
  );
  await saveButton.click();
  const revisedDocument = await (await revisedResponse).json();
  expect(revisedDocument.spec_hash).not.toBe(createdDocument.spec_hash);
  await expect(page).toHaveURL(
    new RegExp(
      `/research/strategies/${strategyId}/revisions/2(?:\\?.*)?$`,
      "u",
    ),
  );
  await expect(
    page.locator(
      `.doc-toolbar__identity code[title="${revisedDocument.spec_hash}"]`,
    ),
  ).toBeVisible();
  await expect(documentStatus).toContainText("저장됨");
  await page.getByRole("tab", { name: "Diff", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "StrategySpec Diff" }),
  ).toContainText("/title");
  await capture(page, "06-revision-diff.png");

  await page.getByRole("tab", { name: "YAML", exact: true }).click();
  await page
    .getByRole("textbox", { name: "종목 ID", exact: true })
    .fill("sec-005930-1, sec-000660-1, sec-035420-1");
  await page
    .getByRole("combobox", { name: "노드", exact: true })
    .selectOption("mom_252");
  await page.getByRole("button", { name: "추적 실행" }).click();
  await expect(page.getByLabel("추적 재현 정보")).toBeVisible({
    timeout: 60_000,
  });
  await page.getByRole("tab", { name: "TargetTape" }).click();
  await expect(
    page.getByRole("region", { name: "TargetTape 후보와 선택 노드 결과" }),
  ).toBeVisible();
  const debuggerHandle = page.getByRole("separator", {
    name: "중간 결과 크기 조절",
  });
  await debuggerHandle.focus();
  await debuggerHandle.press("End");
  await expect(debuggerHandle).toHaveAttribute("aria-valuenow", "480");
  await capture(
    page,
    "07-debug-trace.png",
    page.getByRole("region", { name: "중간 결과" }),
  );

  expect(backtestPosts).toHaveLength(0);
  const settingsToggle = page.getByLabel("실행 설정 열기");
  await settingsToggle.click();
  await page
    .getByRole("combobox", { name: "실행 core" })
    .selectOption("python");
  const initialCash = page.getByRole("spinbutton", { name: "초기 자본 (KRW)" });
  await initialCash.fill("0");
  const rejectedRun = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/backtests",
  );
  await backtestButton.click();
  const rejectedResponse = await rejectedRun;
  expect(rejectedResponse.status()).toBe(422);
  expect(rejectedResponse.request().postDataJSON()).toMatchObject({
    core: "python",
    initial_cash: 0,
  });
  const rejectedPayload = await rejectedResponse.json();
  expect(Array.isArray(rejectedPayload.detail)).toBe(true);
  expect(rejectedPayload.detail).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        loc: ["body"],
        msg: expect.stringContaining("initial_cash must be positive"),
        type: "value_error",
        input: expect.objectContaining({ initial_cash: 0 }),
      }),
    ]),
  );
  expect(backtestPosts).toHaveLength(1);
  expect(new URL(page.url()).pathname).toBe(
    `/research/strategies/${strategyId}/revisions/2`,
  );
  await expect(page.getByRole("alert")).toContainText("백테스트 시작 실패");
  await capture(page, "08-backtest-error.png");

  await initialCash.fill("100000000");
  await page
    .getByRole("textbox", { name: "벤치마크 종목 ID" })
    .fill("sec-005930-1");
  await page.getByRole("spinbutton", { name: "연환산 거래일" }).fill("252");
  await expect(page.getByText("준비됨", { exact: true })).toBeVisible();
  await settingsToggle.click();
  const acceptedRun = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/backtests",
  );
  await backtestButton.click();
  const acceptedResponse = await acceptedRun;
  expect(acceptedResponse.status()).toBe(202);
  expect(acceptedResponse.request().postDataJSON()).toMatchObject({
    core: "python",
    initial_cash: 100_000_000,
    benchmark_security_id: "sec-005930-1",
    annualization_days: 252,
    strategy_source: {
      kind: "saved_revision",
      strategy_id: strategyId,
      revision: 2,
      expected_spec_hash: revisedDocument.spec_hash,
    },
  });
  const acceptedPayload = await acceptedResponse.json();
  const runId = acceptedPayload.run?.run_id;
  if (typeof runId !== "string" || runId === "") {
    throw new Error("백테스트 실행 ID가 202 응답에 없습니다.");
  }
  expect(backtestPosts).toHaveLength(2);
  await expect(page).toHaveURL(`${frontendUrl}/research/backtests/${runId}`);
  await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
    "completed",
    { timeout: 120_000 },
  );
  await expect(
    page.getByRole("heading", { name: "백테스트 결과" }),
  ).toBeVisible();
  await expect(page.getByRole("article", { name: "백테스트 결과" })).toHaveCSS(
    "display",
    "grid",
  );
  await expect(page.getByRole("region", { name: "핵심 성과 지표" })).toHaveCSS(
    "grid-template-columns",
    /^(?!none$).+/u,
  );
  await capture(page, "09-backtest-result.png");

  await page.getByRole("link", { name: "백테스트", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "백테스트 이력" }),
  ).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: runId })).toHaveCount(1);
  await capture(page, "10-backtest-history.png");

  await page.getByRole("link", { name: "전략", exact: true }).click();
  await expect(page.getByRole("heading", { name: "전략 이력" })).toBeVisible();
  await page
    .getByRole("button", {
      name: `Revision 펼치기: ${titleV2} (${strategyId})`,
    })
    .click();
  await expect(
    page.getByRole("region", {
      name: `저장 revision 목록: ${titleV2} (${strategyId})`,
    }),
  ).toContainText("v1");
  await expect(
    page.getByRole("region", {
      name: `저장 revision 목록: ${titleV2} (${strategyId})`,
    }),
  ).toContainText("v2");
  await capture(page, "11-strategy-history.png");

  await page.goto(
    `${frontendUrl}/research/strategies/${strategyId}/revisions/2`,
  );
  await expect(editor).toBeVisible();
  await page.keyboard.press("Control+K");
  await expect(page.getByRole("dialog")).toBeVisible();
  await capture(page, "12-command-palette.png");

  process.stdout.write(`매뉴얼 전략 ID: ${strategyId}\n`);
} finally {
  await context.close();
  await browser.close();
}
