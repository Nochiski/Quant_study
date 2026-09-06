import {
  expect,
  test,
  type Browser,
  type Locator,
  type Page,
} from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  createStrategy,
  explainFactorGraph,
  getBacktestResult,
  getStrategyDocument,
  getStrategyTemplate,
  traceStrategy,
  type BacktestRunSpec,
  type BacktestRunState,
  type BacktestStartResponse,
  type StrategyTraceRequest,
} from "../src/shared/api/generated";
import { createClient } from "../src/shared/api/generated/client";

const BACKEND = "http://localhost:8000";
const apiClient = createClient({ baseUrl: BACKEND });
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
  await expect(page.getByRole("status", { name: "문서 상태" })).toContainText(
    phase,
  );
};

const requireData = <Value>(
  data: Value | undefined,
  operation: string,
): Value => {
  if (data === undefined) throw new Error(`${operation} returned no data`);
  return data;
};

const rowFor = (region: Locator, securityId: string): Locator =>
  region.getByRole("row").filter({ hasText: securityId });

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
    const conflictResponse = conflicting.page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname ===
          `/api/v1/strategy-documents/${strategyId}/revisions` &&
        response.status() === 409,
    );
    await save(conflicting.page).click();
    expect((await conflictResponse).status()).toBe(409);
    const conflict = conflicting.page.getByRole("region", {
      name: "리비전 충돌",
    });
    await expect(conflict).toBeVisible();
    await expect(conflict).toContainText("서버 최신 v3 · 현재 기준 v2");
    await expect.poll(() => currentSource(conflicting.page)).toBe(finalSource);
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
    await expect.poll(() => currentSource(conflicting.page)).toBe(finalSource);

    const savedV4 = requireData(
      (
        await getStrategyDocument({
          client: apiClient,
          path: { strategy_id: strategyId, revision: 4 },
        })
      ).data,
      "get v4 strategy document",
    );
    expect(savedV4.source).toBe(finalSource);
    expect(savedV4.generated).toBe(false);
    expect(savedV4.origin).toBe("document");

    const workflow = conflicting.page;
    const securityIds = ["sec-005930-1", "sec-000660-1"];
    const factor = savedV4.spec.factors.factors[0];
    if (factor === undefined) throw new Error("saved v4 has no factor");
    const traceRequest: StrategyTraceRequest = {
      strategy_source: {
        kind: "saved_revision",
        strategy_id: strategyId,
        revision: 4,
        expected_spec_hash: savedV4.spec_hash,
      },
      security_ids: securityIds,
      factor_id: factor.factor_id,
      node_ids: factor.graph.nodes.map((node) => node.node_id),
      include_raw: true,
      offset: 0,
      limit: factor.graph.nodes.length * securityIds.length,
    };
    await workflow
      .getByRole("textbox", { name: "종목 ID", exact: true })
      .fill(securityIds.join(", "));
    await workflow
      .getByRole("combobox", { name: "노드", exact: true })
      .selectOption("mom_252");
    await expect(
      workflow.getByRole("button", { name: "추적 실행" }),
    ).toBeEnabled();
    const submittedTrace = workflow.waitForRequest(
      (request) =>
        request.method() === "POST" &&
        new URL(request.url()).pathname === "/api/v1/strategies/debug/trace",
    );
    await workflow.getByRole("button", { name: "추적 실행" }).click();
    expect((await submittedTrace).postDataJSON()).toEqual(traceRequest);
    const provenance = workflow.getByLabel("추적 재현 정보");
    await expect(provenance).toBeVisible({ timeout: 60_000 });

    const trace = requireData(
      (await traceStrategy({ client: apiClient, body: traceRequest })).data,
      "trace saved v4",
    );
    expect(trace).toMatchObject({
      spec_hash: savedV4.spec_hash,
      snapshot_id: "mock-equity-v0.2-20260903",
      registry_version: "factor-registry-v1",
      as_of: "2026-07-31",
      factor_id: "momentum",
      raw_truncated: false,
      provenance: {
        kind: "saved_revision",
        spec_hash: savedV4.spec_hash,
        schema_version: "1.0",
        strategy_id: strategyId,
        revision: 4,
        source_hash: savedV4.source_hash,
      },
      trace: { offset: 0, limit: 4, returned: 4, has_more: false },
    });
    expect(trace.spec_hash).toHaveLength(64);
    expect(trace.plan_hash).toHaveLength(64);
    expect(trace.raw.every((row) => row.available_date <= trace.as_of)).toBe(
      true,
    );
    expect(
      trace.raw.map(
        ({ security_id, field_id, value, available_date, kind }) => ({
          security_id,
          field_id,
          value,
          available_date,
          kind,
        }),
      ),
    ).toEqual([
      {
        security_id: "sec-000660-1",
        field_id: "price.close",
        value: 212_570,
        available_date: "2026-07-31",
        kind: "observed",
      },
      {
        security_id: "sec-005930-1",
        field_id: "price.close",
        value: 116_285,
        available_date: "2026-07-31",
        kind: "observed",
      },
    ]);

    const close660 = requireData(
      trace.trace.rows.find(
        (row) => row.node_id === "close" && row.security_id === "sec-000660-1",
      ),
      "close trace for sec-000660-1",
    );
    expect(close660).toEqual({
      node_id: "close",
      operation: "field",
      as_of: "2026-07-31",
      security_id: "sec-000660-1",
      value: 212_570,
      status: "ok",
      inputs: [],
    });
    const momentum660 = requireData(
      trace.trace.rows.find(
        (row) =>
          row.node_id === "mom_252" && row.security_id === "sec-000660-1",
      ),
      "momentum trace for sec-000660-1",
    );
    expect(momentum660).toEqual({
      node_id: "mom_252",
      operation: "time_series.momentum",
      as_of: "2026-07-31",
      security_id: "sec-000660-1",
      value: 0.02667014412117008,
      status: "ok",
      inputs: [{ node_id: "close", value: 212_570 }],
    });

    const targetTrace = requireData(trace.target ?? undefined, "target trace");
    const candidate660 = requireData(
      targetTrace.candidates.find((row) => row.security_id === "sec-000660-1"),
      "candidate for sec-000660-1",
    );
    expect(candidate660).toMatchObject({
      eligible: true,
      selected: true,
      rank: 2,
      side: "long",
      target_weight: 0.05,
      exclusion_reasons: [],
    });
    expect(candidate660.composite_score).toBeCloseTo(0.026670144121170077);
    const construction660 = requireData(
      targetTrace.construction.find(
        (row) => row.security_id === "sec-000660-1",
      ),
      "construction for sec-000660-1",
    );
    expect(construction660).toMatchObject({
      eligible: true,
      selected: true,
      rank: 2,
      side: "long",
      unconstrained_target_weight: 1 / 3,
      constrained_target_weight: 0.05,
      constraint_effect: "adjusted",
      exclusion_reasons: [],
    });
    expect(construction660.factor_contributions).toHaveLength(1);
    expect(construction660.factor_contributions[0]).toMatchObject({
      factor_id: "momentum",
      configured_weight: 0.6,
      direction: "high",
      status: "ok",
    });
    expect(
      construction660.factor_contributions[0]?.normalized_contribution,
    ).toBeCloseTo(0.026670144121170077);

    await expect(provenance).toContainText("mock-equity-v0.2-20260903");
    await expect(provenance).toContainText("factor-registry-v1");
    await expect(provenance).toContainText("2026-07-31");
    await expect(provenance.getByTitle(trace.spec_hash)).toBeVisible();
    await expect(provenance.getByTitle(trace.plan_hash)).toBeVisible();
    const linked = workflow.getByRole("tabpanel", { name: "연결 추적" });
    const linkedList = linked.getByRole("list", { name: "연결 추적" });
    const pipeline660 = linkedList.getByRole("listitem", {
      name: "sec-000660-1",
      exact: true,
    });
    await expect(pipeline660).toContainText("212,570");
    await expect(pipeline660).toContainText("0.02667014");
    await expect(pipeline660).toContainText("순위 2 · long");
    await expect(pipeline660).toContainText("33.3333%");
    await expect(pipeline660).toContainText("5.00%");
    await expect(pipeline660).toContainText("adjusted");

    await workflow.getByRole("tab", { name: "TargetTape" }).click();
    const target = workflow.getByRole("region", {
      name: "TargetTape 후보와 선택 노드 결과",
    });
    const target660 = rowFor(target, "sec-000660-1");
    await expect(target660).toContainText("0.02667014");
    await expect(target660).toContainText("2");
    await expect(target660).toContainText("예");
    await expect(target660).toContainText("5.00%");
    await expect(target660).toContainText("ok");
    await workflow.getByRole("tab", { name: "원시 데이터" }).click();
    const raw = workflow.getByRole("region", {
      name: "원시 필드 값, 공개일과 데이터 상태",
    });
    const raw660 = rowFor(raw, "sec-000660-1");
    await expect(raw660).toContainText("price.close");
    await expect(raw660).toContainText("212,570");
    await expect(raw660).toContainText("2026-07-31");
    await expect(raw660).toContainText("observed");
    await workflow.getByRole("tab", { name: "선택 노드" }).click();
    const selectedNode = workflow.getByRole("region", {
      name: "선택한 FactorGraph 노드의 실제 계산 결과",
    });
    const selected660 = rowFor(selectedNode, "sec-000660-1");
    await expect(selected660).toContainText("mom_252");
    await expect(selected660).toContainText("time_series.momentum");
    await expect(selected660).toContainText("close=212,570");
    await expect(selected660).toContainText("0.02667014");
    await expect(selected660).toContainText("ok");

    const explanation = requireData(
      (
        await explainFactorGraph({
          client: apiClient,
          body: {
            graph: factor.graph,
            parameter_ids: (savedV4.spec.parameters ?? []).map(
              (parameter) => parameter.parameter_id,
            ),
            factor_ids: savedV4.spec.factors.factors.map(
              (item) => item.factor_id,
            ),
            subgraph_ids: [],
          },
        })
      ).data,
      "explain v4 factor graph",
    );
    const plan = requireData(explanation.plan ?? undefined, "factor plan");
    expect(plan.plan_hash).toBe(trace.plan_hash);
    expect(plan).toMatchObject({
      registry_version: trace.registry_version,
      minimum_history_sessions: 252,
      as_of_policy: "available_date_lte_as_of",
      missing_policy: "drop",
      required_field_ids: ["price.close"],
      output_node_id: "mom_252",
    });
    expect(plan.graph_hash).toHaveLength(64);
    await workflow.getByRole("tab", { name: "실행 계획" }).click();
    const planPanel = workflow.getByRole("tabpanel", { name: "실행 계획" });
    await expect(
      planPanel.getByText("252 세션", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      planPanel.getByText("available_date_lte_as_of", { exact: false }),
    ).toBeVisible();
    await expect(planPanel.getByText("drop", { exact: true })).toBeVisible();
    await expect(planPanel.getByTitle(plan.graph_hash)).toBeVisible();
    await expect(planPanel.getByTitle(plan.plan_hash)).toBeVisible();
    for (const step of plan.steps) {
      const planRow = planPanel
        .getByRole("row")
        .filter({ hasText: step.node_id })
        .filter({ hasText: step.operation });
      await expect(planRow).toContainText(step.operation);
      await expect(planRow).toContainText(
        `${step.minimum_history_sessions} 세션`,
      );
      for (const input of step.input_node_ids)
        await expect(planRow).toContainText(input);
    }
    await expect(
      planPanel.getByText("price.close", { exact: true }),
    ).toBeVisible();

    const settingsToggle = workflow.getByLabel("실행 설정 열기");
    await settingsToggle.click();
    const core = workflow.getByRole("combobox", { name: "실행 core" });
    await core.selectOption("python");
    await expect(core).toHaveValue("python");
    await core.selectOption("rust");
    const initialCash = workflow.getByRole("spinbutton", {
      name: "초기 자본 (KRW)",
    });
    await initialCash.fill("0");
    await expect(workflow.getByRole("alert")).toContainText(
      "초기 자본은 0보다 큰 숫자여야 합니다.",
    );
    await expect(backtest(workflow)).toBeDisabled();
    await initialCash.fill("123456789");
    await workflow
      .getByRole("textbox", { name: "벤치마크 종목 ID" })
      .fill("sec-005930-1");
    await workflow
      .getByRole("spinbutton", { name: "연환산 거래일" })
      .fill("260");
    await workflow.getByLabel("OOS 시작일 (선택)").fill("2025-01-02");
    await expect(workflow.getByText("준비됨", { exact: true })).toBeVisible();
    await settingsToggle.click();

    const expectedRunRequest: BacktestRunSpec = {
      core: "rust",
      initial_cash: 123_456_789,
      benchmark_security_id: "sec-005930-1",
      annualization_days: 260,
      metric_windows: [
        {
          scope: "out_of_sample",
          start: "2025-01-02",
          end: "2026-08-31",
          label: "OOS 2025-01-02",
        },
      ],
      strategy_source: {
        kind: "saved_revision",
        strategy_id: strategyId,
        revision: 4,
        expected_spec_hash: savedV4.spec_hash,
      },
    };
    await expect(backtest(workflow)).toBeEnabled();
    const submittedRun = workflow.waitForRequest(
      (request) =>
        request.method() === "POST" &&
        new URL(request.url()).pathname === "/api/v1/backtests",
    );
    await backtest(workflow).click();
    expect((await submittedRun).postDataJSON()).toEqual(expectedRunRequest);
    await expect(workflow).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    const runId = new URL(workflow.url()).pathname.split("/").at(-1)!;
    await expect(
      workflow.getByRole("status", { name: "실행 상태" }),
    ).toContainText("completed", { timeout: 120_000 });
    await expect(
      workflow.getByRole("heading", { name: "백테스트 결과" }),
    ).toBeVisible({ timeout: 120_000 });
    const result = requireData(
      (
        await getBacktestResult({
          client: apiClient,
          path: { run_id: runId },
        })
      ).data,
      "get completed backtest result",
    );
    expect(result.manifest).toMatchObject({
      run_id: runId,
      engine_core: "rust",
      initial_cash: 123_456_789,
      annualization_days: 260,
      strategy_hash: savedV4.spec_hash,
      strategy_provenance: {
        kind: "saved_revision",
        strategy_id: strategyId,
        revision: 4,
        spec_hash: savedV4.spec_hash,
        source_hash: savedV4.source_hash,
      },
      run_spec: expectedRunRequest,
    });
    expect(result.manifest.run_spec.strategy?.title).toBe(finalTitle);
    expect(result.manifest.run_fingerprint).toHaveLength(64);
    expect(result.manifest.target_tape_hash).toHaveLength(64);
    expect(result.manifest.data_snapshot_id).toBe("mock-equity-v0.2-20260903");
    await expect(workflow.getByText(/RUST core · registry/u)).toBeVisible();
    await expect(
      workflow.getByRole("button", { name: "동일 설정 재실행" }),
    ).toBeEnabled();
    const manifest = workflow.getByLabel(
      "Manifest · 데이터 경고 · 재현성 정보",
    );
    await manifest
      .getByText("Manifest · 데이터 경고 · 재현성 정보", { exact: true })
      .click();
    await expect(manifest).toContainText("RUST");
    await expect(manifest).toContainText("123,456,789");
    await expect(manifest).toContainText("sec-005930-1");
    await expect(manifest).toContainText("260");
    await expect(manifest).toContainText(
      "out_of_sample: 2025-01-02 → 2026-08-31",
    );
    await expect(manifest).toContainText(`${strategyId} r4`);
    await expect(
      manifest.getByTitle(result.manifest.run_fingerprint),
    ).toBeVisible();
    await expect(
      manifest.getByTitle(result.manifest.strategy_hash).last(),
    ).toBeVisible();
    await expect(
      manifest.getByTitle(result.manifest.target_tape_hash),
    ).toBeVisible();

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

    for (const revision of [1, 2, 3, 4]) {
      if (!(await revisions.isVisible())) {
        await workflow
          .getByRole("button", {
            name: `Revision 펼치기: ${finalTitle} (${strategyId})`,
          })
          .click();
        await expect(revisions).toBeVisible();
      }
      const revisionRow = revisions
        .getByRole("row")
        .filter({ hasText: `v${revision}` });
      const diffLink = revisionRow.getByRole("link", { name: "Diff" });
      const expectedHref = `/research/strategies/${strategyId}/revisions/${revision}?view=diff`;
      await expect(diffLink).toHaveAttribute("href", expectedHref);
      await diffLink.click();
      const navigated = new URL(workflow.url());
      expect(`${navigated.pathname}${navigated.search}`).toBe(expectedHref);
      const immutableDiff = workflow.getByRole("region", {
        name: "StrategySpec Diff",
      });
      await expect(immutableDiff).toBeVisible();
      await expect(immutableDiff.getByLabel("기준 revision")).toHaveValue(
        String(Math.max(1, revision - 1)),
      );
      await expect(immutableDiff.getByLabel("대상 revision")).toHaveValue(
        String(revision),
      );
      await workflow.goBack();
      await expect(
        workflow.getByRole("heading", { name: "전략 이력" }),
      ).toBeVisible();
    }

    await conflicting.context.close();
  });

  test("cancels a nonterminal run and replays the server-owned request byte-for-byte", async ({
    page,
  }) => {
    const acceptedRequest: BacktestRunSpec = {
      core: "python",
      initial_cash: 321_000_000,
      benchmark_security_id: "sec-benchmark",
      annualization_days: 260,
      metric_windows: [
        {
          scope: "out_of_sample",
          start: "2025-01-02",
          end: "2026-08-31",
          label: "desk OOS",
        },
      ],
      strategy_source: {
        kind: "saved_revision",
        strategy_id: "strategy-cancel-e2e",
        revision: 7,
        expected_spec_hash: "a".repeat(64),
      },
    };
    const runState = (
      runId: string,
      status: BacktestRunState["status"],
    ): BacktestRunState => ({
      run_id: runId,
      status,
      progress: status === "running" || status === "cancel_requested" ? 0.4 : 0,
      stage: status,
      message: status,
      created_at: "2026-09-06T00:00:00Z",
      updated_at: "2026-09-06T00:00:01Z",
    });
    let cancellationRequested = false;
    let requestReads = 0;
    let replayed = false;

    await page.route("**/api/v1/backtests/**", async (route) => {
      const { pathname } = new URL(route.request().url());
      if (pathname.endsWith("/request")) {
        requestReads += 1;
        await route.fulfill({ status: 200, json: acceptedRequest });
        return;
      }
      if (pathname === "/api/v1/backtests/run-cancellable/cancel") {
        cancellationRequested = true;
        await route.fulfill({
          status: 202,
          json: runState("run-cancellable", "cancel_requested"),
        });
        return;
      }
      if (pathname === "/api/v1/backtests/run-cancellable") {
        await route.fulfill({
          status: 200,
          json: runState(
            "run-cancellable",
            cancellationRequested ? "cancelled" : "running",
          ),
        });
        return;
      }
      if (pathname === "/api/v1/backtests/run-replayed") {
        await route.fulfill({
          status: 200,
          json: runState("run-replayed", "queued"),
        });
        return;
      }
      await route.abort("failed");
    });
    await page.route("**/api/v1/backtests", async (route) => {
      expect(route.request().method()).toBe("POST");
      expect(route.request().postDataJSON()).toEqual(acceptedRequest);
      replayed = true;
      const response: BacktestStartResponse = {
        run: runState("run-replayed", "queued"),
      };
      await route.fulfill({ status: 202, json: response });
    });

    const navigation = await page.goto("/research/backtests/run-cancellable");
    expect(navigation?.ok()).toBe(true);
    await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
      "running",
    );
    await page.getByRole("button", { name: "실행 취소" }).click();
    await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
      "cancelled",
    );
    const rerun = page.getByRole("button", { name: "동일 설정 재실행" });
    await expect(rerun).toBeEnabled();
    await rerun.click();
    await expect(page).toHaveURL("/research/backtests/run-replayed");
    expect(requestReads).toBeGreaterThanOrEqual(1);
    expect(replayed).toBe(true);
  });

  test("migrates a source-less legacy revision without changing meaning", async ({
    page,
  }) => {
    const template = requireData(
      (await getStrategyTemplate({ client: apiClient })).data,
      "get legacy strategy template",
    );
    const saved = requireData(
      (await createStrategy({ client: apiClient, body: template })).data,
      "create legacy strategy",
    );
    const strategyId = saved.spec.identity.strategy_id;
    const generated = requireData(
      (
        await getStrategyDocument({
          client: apiClient,
          path: { strategy_id: strategyId, revision: 1 },
        })
      ).data,
      "get generated legacy document",
    );
    expect(generated.generated).toBe(true);
    expect(generated.origin).toBe("legacy_json");
    expect(generated.spec_hash).toBe(saved.spec_hash);

    await openEditor(page, `/research/strategies/${strategyId}/revisions/1`);
    await expect(page.getByText("legacy JSON에서 생성된 문서")).toBeVisible();
    const whitespaceOnly = `${generated.source.trimEnd()}\n\n`;
    await replaceSource(page, whitespaceOnly);
    await expectPhase(page, "검증 통과");
    await saveAndWaitForRevision(page, 2);
    const migrated = requireData(
      (
        await getStrategyDocument({
          client: apiClient,
          path: { strategy_id: strategyId, revision: 2 },
        })
      ).data,
      "get migrated strategy document",
    );
    expect(migrated.generated).toBe(false);
    expect(migrated.origin).toBe("document");
    expect(migrated.source).toBe(whitespaceOnly);
    expect(migrated.spec_hash).toBe(saved.spec_hash);
  });
});
