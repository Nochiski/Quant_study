import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";

import {
  createExperiment,
  createStrategyDocument,
  getExperiment,
  listExperimentTrials,
  type BacktestRunSpec,
  type StrategyTraceRequest,
  type StrategyTraceResponse,
} from "../../src/shared/api/generated";
import {
  apiClient,
  fillRunEnvironment,
  GOLDEN,
  mustReplace,
  openEditorWorkspace,
  openTraceSource,
  openTraceWorkspace,
  REQUESTED_ENVIRONMENT,
  requireData,
} from "../workbench-helpers";

// 실제 팩터 출력에서 읽는 배율이다. 문서의 기본값은 1, 실험의 두 칸은 0과 1이다.
const PARAMETER_SOURCE = mustReplace(mustReplace(GOLDEN,
  "      output_node_id: mom_252",
  [
    "        - kind: parameter", "          node_id: scale_value", "          parameter_id: scale",
    "        - kind: binary", "          node_id: scaled", "          operator: multiply",
    "          left_node_id: mom_252", "          right_node_id: scale_value", "      output_node_id: scaled",
  ].join("\n")), "parameters: []", [
  "parameters:", "  - parameter_id: scale", "    kind: float", "    default: 1.0",
  "    minimum: 0.0", "    maximum: 1.0", "    step: 1.0",
].join("\n"));

const centerIsVisible = (target: Locator) => target.evaluate(element => {
  const box = element.getBoundingClientRect();
  const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
  return hit !== null && element.contains(hit);
});

const traceResponse = (page: Page, summaryOnly: boolean) => page.waitForResponse(response => {
  if (!response.url().endsWith("/api/v1/strategies/debug/trace") || response.request().method() !== "POST") return false;
  const body = response.request().postDataJSON() as StrategyTraceRequest;
  return summaryOnly ? body.security_ids.length === 0 : body.security_ids.length === 3 && body.node_ids?.includes("scaled") === true;
});

const scenario = async (page: Page, testInfo: TestInfo, width: number, dark: boolean) => {
  await page.setViewportSize({ width, height: 640 });
  await page.emulateMedia({ colorScheme: dark ? "dark" : "light" });
  const saved = requireData((await createStrategyDocument({ client: apiClient, body: {
    format: "yaml", source: mustReplace(PARAMETER_SOURCE, "퀄리티 모멘텀", `US-CS-03 선택값 ${width} ${Date.now()}`),
  } })).data, "선택값 전략 저장");
  const experimentId = requireData((await createExperiment({ client: apiClient, body: {
    run: {
      strategy_source: { kind: "saved_revision", strategy_id: saved.strategy_id, revision: saved.revision, expected_spec_hash: saved.spec_hash },
      environment: { ...REQUESTED_ENVIRONMENT, start: "2021-01-04", end: "2022-12-30" },
    },
    search: { scale: null },
    split: { mode: "rolling", train_years: 1, test_years: 1, embargo_sessions: 0 },
  } })).data, "선택값 실험 시작").record.experiment_id;
  await expect.poll(async () => requireData((await getExperiment({ client: apiClient,
    path: { experiment_id: experimentId },
  })).data, "실험 완료 조회").settled, { timeout: 60_000 }).toBe(true);
  const trials = requireData((await listExperimentTrials({ client: apiClient,
    path: { experiment_id: experimentId },
  })).data, "완료한 실험 칸 조회");
  expect(trials).toHaveLength(2);
  for (const trial of trials) expect(trial.status).toBe("completed");

  const choose = async (value: number) => {
    const runId = trials.find(state => state.trial.parameter_values.scale === value)?.attempts.at(-1)?.run_id;
    expect(runId).toBeTruthy();
    await page.goto(`/research/experiments/${experimentId}`);
    const row = page.getByRole("region", { name: "trial 목록", exact: true }).getByRole("row")
      .filter({ has: page.getByRole("cell", { name: `scale ${value}`, exact: true }) });
    await row.getByRole("link", { name: "이 설정 추적", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`run=${runId}`));
    await fillRunEnvironment(page);
    await expect(page.getByRole("button", { name: "백테스트", exact: true })).toBeEnabled();
    const selection = page.getByRole("region", { name: "선택한 실행의 파라미터", exact: true });
    const summary = selection.locator("summary");
    await summary.focus();
    await summary.press("Space");
    await expect(selection).toContainText(runId!);
    await expect(selection).toContainText("현재 문서와 현재 실행 환경");
    await expect(selection.locator("dt", { hasText: /^scale$/u }).locator("..").locator("dd")).toHaveText(String(value));
    await summary.press("Space");
    return runId;
  };
  const preview = page.getByRole("region", { name: "선정 미리보기", exact: true });
  const refreshPreview = async (value: number | undefined) => {
    const response = traceResponse(page, true);
    await preview.getByRole("button", { name: "미리보기 새로고침", exact: true }).click();
    const received = await response;
    expect(received.ok()).toBe(true);
    const body = received.request().postDataJSON() as StrategyTraceRequest;
    expect(body.parameter_values).toEqual(value === undefined ? undefined : { scale: value });
    expect(body.environment).toEqual(REQUESTED_ENVIRONMENT);
    const data = await received.json() as StrategyTraceResponse;
    expect(data.parameter_values).toEqual({ scale: value ?? 1 });
    await expect(preview.getByRole("table", { name: "팩터 상위 5개 (방향 기준)", exact: true })).toBeVisible();
    return data;
  };
  const runTrace = async (value: number) => {
    await openTraceWorkspace(page);
    const panel = page.getByRole("region", { name: "중간 결과", exact: true });
    const form = panel.getByRole("form", { name: "전략 추적 범위" });
    if (!(await form.isVisible())) await panel.locator("summary").filter({ hasText: "전략 추적 범위" }).click();
    await form.getByRole("textbox", { name: "종목 ID", exact: true }).fill("sec-005930-1, sec-000660-1, sec-035420-1");
    await form.getByRole("combobox", { name: "팩터", exact: true }).selectOption("momentum");
    const node = form.getByRole("combobox", { name: "노드", exact: true });
    await node.selectOption("scaled");
    const button = form.getByRole("button", { name: "추적 실행", exact: true });
    // 낮은 화면에서는 입력 폼 자체가 스크롤 owner다. 다음 네이티브 입력으로 이동해 버튼을 드러낸다.
    await node.focus();
    await node.press("Tab");
    await expect(button).toBeFocused();
    await expect.poll(() => centerIsVisible(button)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`selected-${value}-form-${width}.png`) });
    const response = traceResponse(page, false);
    await button.click();
    const received = await response;
    expect(received.ok()).toBe(true);
    expect((received.request().postDataJSON() as StrategyTraceRequest).parameter_values).toEqual({ scale: value });
    const data = await received.json() as StrategyTraceResponse;
    expect(data.parameter_values).toEqual({ scale: value });
    await expect(panel.getByRole("tabpanel", { name: "연결 추적", exact: true })).toBeVisible();
    await openTraceSource(page);
    const resolved = panel.locator("summary").filter({ hasText: "이번 평가에 사용한 파라미터" });
    await resolved.click();
    await expect(resolved.locator("..").locator("dd")).toHaveText(String(value));
    await page.screenshot({ path: testInfo.outputPath(`selected-${value}-${width}.png`) });
    await panel.locator("summary").filter({ hasText: "추적 출처" }).click();
    const scopeSummary = panel.locator("summary").filter({ hasText: "전략 추적 범위" });
    if (await form.isVisible()) await scopeSummary.click();
    const result = panel.getByRole("tabpanel", { name: "연결 추적", exact: true });
    const list = result.getByRole("list", { name: "연결 추적", exact: true });
    await list.focus();
    await list.press("Home");
    await expect(list.getByRole("listitem").first()).toContainText("scaled");
    await expect(result).toBeVisible();
    // 선택 출처는 접어서 유지하고, 첫 카드의 원천 값까지 바로 읽을 공간을 확보한다.
    const listBounds = await list.boundingBox();
    expect(listBounds).not.toBeNull();
    expect(listBounds!.height).toBeGreaterThanOrEqual(120);
    const firstCard = list.getByRole("listitem").first();
    const firstValue = firstCard.locator(".strategy-debugger__stage-values .strategy-debugger__numeric").first();
    await expect(firstValue).toHaveText(/\d/u);
    await expect.poll(() => centerIsVisible(firstValue)).toBe(true);
    const valueBounds = await firstValue.boundingBox();
    expect(valueBounds).not.toBeNull();
    expect(valueBounds!.y).toBeGreaterThanOrEqual(listBounds!.y);
    expect(valueBounds!.y + valueBounds!.height).toBeLessThanOrEqual(listBounds!.y + listBounds!.height);
    await page.screenshot({ path: testInfo.outputPath(`selected-${value}-results-${width}.png`) });
    await openEditorWorkspace(page);
    // 노드 선택은 기존 outline 정책에 따라 YAML을 연다. 미리보기 과업으로 돌아갈 때 Graph를 명시한다.
    await page.getByRole("tab", { name: "그래프", exact: true }).click();
    const pipeline = page.getByRole("button", { name: "전략 구성으로 돌아가기", exact: true });
    if (await pipeline.isVisible()) await pipeline.click();
    return data;
  };

  await choose(0);
  const zeroPreview = await refreshPreview(0);
  expect(zeroPreview.factor_preview.top.length).toBeGreaterThan(0);
  expect(zeroPreview.factor_preview.top.every(row => row.value === 0)).toBe(true);
  const zeroTrace = await runTrace(0);
  await choose(1);
  // 선택 실행이 바뀌면 앞 칸의 평가 값을 재사용하지 않고 새로 명시한 요청을 보낸다.
  await expect(preview.getByRole("table", { name: "팩터 상위 5개 (방향 기준)", exact: true })).toHaveCount(0);
  const onePreview = await refreshPreview(1);
  expect(onePreview.spec_hash).toBe(zeroPreview.spec_hash);
  expect(onePreview.plan_hash).toBe(zeroPreview.plan_hash);
  expect(onePreview.factor_preview.top.some(row => row.value !== 0)).toBe(true);
  const oneTrace = await runTrace(1);
  expect(oneTrace.spec_hash).toBe(zeroTrace.spec_hash);
  expect(oneTrace.plan_hash).toBe(zeroTrace.plan_hash);

  await page.getByRole("button", { name: "문서 기본값으로 돌아가기", exact: true }).click();
  await expect(page).not.toHaveURL(/[?&]run=/u);
  await expect(page.getByRole("region", { name: "선택한 실행의 파라미터", exact: true })).toHaveCount(0);
  await refreshPreview(undefined);
  await choose(0);
  const submitted = page.waitForRequest(request => request.method() === "POST" && new URL(request.url()).pathname === "/api/v1/backtests");
  await page.getByRole("button", { name: "백테스트", exact: true }).click();
  const request = (await submitted).postDataJSON() as BacktestRunSpec;
  expect(request.parameter_values).toEqual({ scale: 0 });
  expect(request.environment).toEqual(REQUESTED_ENVIRONMENT);
  await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
};

test("US-CS-03 실험의 선택값을 현재 문서의 미리보기와 추적에 적용하고 기본값으로 돌아간다", { tag: ["@story", "@US-CS-03"] }, async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  await scenario(page, testInfo, 1440, false);
});

test("US-CS-03 좁고 낮은 다크 화면에서 선택값 출처와 평가 결과를 읽고 기본값으로 돌아간다", { tag: ["@story", "@US-CS-03"] }, async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  await scenario(page, testInfo, 360, true);
});
