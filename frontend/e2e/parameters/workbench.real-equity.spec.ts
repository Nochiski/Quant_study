/**
 * 기존 real-equity opt-in project와 격리 서버·DB를 쓰는 기술 QA다. 실행 여부는 ROOT가 결정한다.
 * 2024-01-08~12의 선택 파라미터 전달과 표시만 확인하며 수익성 판단·원장 파일 직접 조회를 하지 않는다.
 * 파일명은 기존 project의 testMatch를 따른다. 릴리스 스토리 게이트에는 포함하지 않는다.
 */
import { expect, test } from "@playwright/test";

import {
  createStrategyDocument,
  getBacktestRequest,
  getBacktestStatus,
  startBacktest,
  type StrategyTraceRequest,
  type StrategyTraceResponse,
} from "../../src/shared/api/generated";
import {
  apiClient,
  fillRunEnvironment,
  GOLDEN,
  mustReplace,
  openTraceSource,
  openTraceWorkspace,
  requireData,
} from "../workbench-helpers";

const ENVIRONMENT = { start: "2024-01-08", end: "2024-01-12", universe_id: "krx.common-stock" };
const AS_OF = "2024-01-11";
const SECURITIES = ["005930:1", "000660:1", "035420:1"];

// backend test_run_parameter_values._spec과 같은 3세션 모멘텀 × scale, 매 세션 한 종목 선정이다.
const SOURCE = [
  ["field_id: price.adj_close", "field_id: price.close"],
  ["window: 252", "window: 3"],
  ["selection_count: 20", "selection_count: 1"],
  ["rebalance: monthly", "rebalance: every_n_sessions\n  rebalance_every_n_sessions: 1"],
  ["max_name_weight: 0.05", "max_name_weight: 1.0"],
  ["      output_node_id: mom_252", [
    "        - kind: parameter", "          node_id: scale", "          parameter_id: scale",
    "        - kind: binary", "          node_id: scaled", "          operator: multiply",
    "          left_node_id: mom_252", "          right_node_id: scale", "      output_node_id: scaled",
  ].join("\n")],
  ["parameters: []", [
    "parameters:", "  - parameter_id: scale", "    kind: float", "    default: 1.0",
    "    minimum: -1.0", "    maximum: 1.0",
  ].join("\n")],
].reduce((source, [before, after]) => mustReplace(source, before!, after!), GOLDEN);

test.describe("선택 파라미터 실자료 기술 QA", () => {
  test.skip((process.env.E2E_REAL_EQUITY_ROOT ?? "") === "", "E2E_REAL_EQUITY_ROOT가 없는 opt-in 환경이다");
  // 기존 실자료 시나리오와 같은 완료 예산이다. 짧은 구간이어도 자료 적재 시간을 임의로 단축하지 않는다.
  test.setTimeout(900_000);

  test("저장 실행의 선택값을 GET 원본에서 읽어 현재 환경의 미리보기와 추적에 동일하게 적용한다", async ({ page }, testInfo) => {
    const saved = requireData((await createStrategyDocument({ client: apiClient, body: {
      format: "yaml", source: mustReplace(SOURCE, "퀄리티 모멘텀", `선택값 실자료 기술 QA ${Date.now()}`),
    } })).data, "격리 전략 저장");
    const reference = { kind: "saved_revision" as const, strategy_id: saved.strategy_id,
      revision: saved.revision, expected_spec_hash: saved.spec_hash };
    let staticPlanHash: string | undefined;

    for (const scale of [-1, 1]) {
      const runId = requireData((await startBacktest({ client: apiClient, body: {
        strategy_source: reference, environment: ENVIRONMENT, parameter_values: { scale },
      } })).data, "짧은 선택값 실행 수락").run.run_id;
      await expect.poll(async () => {
        const state = requireData((await getBacktestStatus({ client: apiClient, path: { run_id: runId } })).data, "선택값 실행 완료 조회");
        expect(state.error, JSON.stringify(state.error)).toBeNull();
        return state.status;
      }, { timeout: 420_000, intervals: [2_000] }).toBe("completed");
      const original = requireData((await getBacktestRequest({ client: apiClient, path: { run_id: runId } })).data, "원본 실행 요청");
      expect(original.strategy_source).toEqual(reference);
      expect(original.parameter_values).toEqual({ scale });

      const selectedRequest = page.waitForResponse(response => response.ok() &&
        new URL(response.url()).pathname === `/api/v1/backtests/${runId}/request`);
      await page.goto(`/research/strategies/${saved.strategy_id}/revisions/${saved.revision}?view=graph&run=${runId}`);
      expect(await (await selectedRequest).json()).toEqual(original);
      await fillRunEnvironment(page, {}, ENVIRONMENT);
      const selection = page.getByRole("region", { name: "선택한 실행의 파라미터", exact: true });
      const sourceSummary = selection.locator("summary");
      await sourceSummary.focus();
      await sourceSummary.press("Space");
      await expect(selection).toContainText(runId);
      await expect(selection).toContainText("현재 문서와 현재 실행 환경");
      await expect(selection.locator("dt", { hasText: /^scale$/u }).locator("..").locator("dd")).toHaveText(String(scale));
      await sourceSummary.press("Space");

      const preview = page.getByRole("region", { name: "선정 미리보기", exact: true });
      await preview.getByLabel("기준일 (선택)", { exact: true }).fill(AS_OF);
      const waitTrace = (summaryOnly: boolean) => page.waitForResponse(response => {
        if (!response.url().endsWith("/api/v1/strategies/debug/trace") || response.request().method() !== "POST") return false;
        const body = response.request().postDataJSON() as StrategyTraceRequest;
        return summaryOnly ? body.security_ids.length === 0
          : body.security_ids.length === SECURITIES.length && body.node_ids?.includes("scaled") === true;
      });
      const previewResponse = waitTrace(true);
      await preview.getByRole("button", { name: "미리보기 새로고침", exact: true }).click();
      const received = await previewResponse;
      expect(received.ok()).toBe(true);
      expect(received.request().postDataJSON()).toMatchObject({ parameter_values: original.parameter_values, environment: ENVIRONMENT, as_of: AS_OF });
      const evaluated = await received.json() as StrategyTraceResponse;
      expect(evaluated.parameter_values).toEqual({ scale });
      expect(evaluated.factor_preview.top.length).toBeGreaterThan(0);
      expect(evaluated.spec_hash).toBe(saved.spec_hash);
      if (staticPlanHash !== undefined) expect(evaluated.plan_hash).toBe(staticPlanHash);
      staticPlanHash = evaluated.plan_hash;
      const table = preview.getByRole("table", { name: "팩터 상위 5개 (방향 기준)", exact: true });
      // 화면의 값은 서버 원수치 그대로다. 여기서 모멘텀이나 순위를 다시 계산하지 않는다.
      for (const [index, row] of evaluated.factor_preview.top.entries()) {
        await expect(table.locator("tbody tr").nth(index).getByRole("cell").nth(1)).toHaveText(String(row.value));
      }
      const previewValues = preview.locator("summary").filter({ hasText: "이번 평가에 사용한 파라미터" });
      await previewValues.click();
      await expect(previewValues.locator("..").locator("dd")).toHaveText(String(scale));
      await page.screenshot({ path: testInfo.outputPath(`real-selected-${scale}-preview.png`) });

      await openTraceWorkspace(page);
      const panel = page.getByRole("region", { name: "중간 결과", exact: true });
      const form = panel.getByRole("form", { name: "전략 추적 범위" });
      await form.getByRole("textbox", { name: "종목 ID", exact: true }).fill(SECURITIES.join(", "));
      await form.getByRole("combobox", { name: "팩터", exact: true }).selectOption("momentum");
      await form.getByRole("combobox", { name: "노드", exact: true }).selectOption("scaled");
      await form.getByLabel("기준일", { exact: true }).fill(AS_OF);
      const traceResponse = waitTrace(false);
      await form.getByRole("button", { name: "추적 실행", exact: true }).click();
      const traced = await traceResponse;
      expect(traced.ok()).toBe(true);
      expect(traced.request().postDataJSON()).toMatchObject({ parameter_values: original.parameter_values, environment: ENVIRONMENT, as_of: AS_OF, security_ids: SECURITIES });
      const data = await traced.json() as StrategyTraceResponse;
      expect(data.parameter_values).toEqual(evaluated.parameter_values);
      expect(data.spec_hash).toBe(evaluated.spec_hash);
      expect(data.plan_hash).toBe(evaluated.plan_hash);
      expect(data.trace.rows.some(row => row.value !== null)).toBe(true);
      await panel.getByRole("tab", { name: "선택 노드", exact: true }).click();
      const nodeTable = panel.getByRole("tabpanel", { name: "선택 노드", exact: true }).getByRole("table");
      const formatter = new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 });
      const selectedRows = data.trace.rows.filter(row => row.node_id === "scaled");
      expect(selectedRows).toHaveLength(SECURITIES.length);
      for (const row of selectedRows) {
        const displayed = nodeTable.getByRole("row").filter({ has: page.getByRole("cell", { name: row.security_id, exact: true }) });
        await expect(displayed.getByRole("cell").nth(4)).toHaveText(row.value === null ? "—" : typeof row.value === "number" ? formatter.format(row.value) : String(row.value));
      }
      await openTraceSource(page);
      const traceValues = panel.locator("summary").filter({ hasText: "이번 평가에 사용한 파라미터" });
      await traceValues.click();
      await expect(traceValues.locator("..").locator("dd")).toHaveText(String(scale));
      await page.screenshot({ path: testInfo.outputPath(`real-selected-${scale}-trace.png`) });
    }
  });
});
