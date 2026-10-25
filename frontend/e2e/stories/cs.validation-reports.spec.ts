/** 용량은 실제 mock 실행/원장을, 회귀 숫자 표시는 별도 명시 합성 응답을 대조한다. */
import { expect, test } from "@playwright/test";
import {
  createStrategyDocument,
  getBacktestResult,
  getBacktestStatus,
  getExperiment,
  getExperimentCapacity,
  getTrialLedger,
  startBacktest,
  type RunFactorRegression,
} from "../../src/shared/api/generated";
import {
  apiClient,
  GOLDEN,
  mustReplace,
  REQUESTED_ENVIRONMENT,
  requireData,
} from "../workbench-helpers";

test(
  "US-CS-11 용량 확인은 시도 수를 늘리지 않고 금액별 비용과 한계 사유를 보인다",
  { tag: ["@story", "@US-CS-11"] },
  async ({ page }, info) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 360, height: 800 });
    await page.emulateMedia({ colorScheme: "dark" });
    const saved = requireData(
      (
        await createStrategyDocument({
          client: apiClient,
          body: {
            format: "yaml",
            source: mustReplace(
              GOLDEN,
              "퀄리티 모멘텀",
              `용량 확인 ${Date.now()}`,
            ),
          },
        })
      ).data,
      "용량 기반 저장",
    );
    const request = {
      strategy_source: {
        kind: "saved_revision" as const,
        strategy_id: saved.strategy_id,
        revision: saved.revision,
        expected_spec_hash: saved.spec_hash,
      },
      environment: REQUESTED_ENVIRONMENT,
    };
    const runId = requireData(
      (await startBacktest({ client: apiClient, body: request })).data,
      "기반 실행",
    ).run.run_id;
    await expect
      .poll(
        async () =>
          requireData(
            (
              await getBacktestStatus({
                client: apiClient,
                path: { run_id: runId },
              })
            ).data,
            "기반 상태",
          ).status,
      )
      .toBe("completed");
    const ledger = () =>
      getTrialLedger({
        client: apiClient,
        path: { strategy_id: saved.strategy_id },
      });
    const before = requireData((await ledger()).data, "실행 전 원장");
    await page.goto(`/research/backtests/${runId}`);
    const capacity = page.getByRole("region", {
      name: "용량 확인",
      exact: true,
    });
    const disclosure = capacity.locator("summary");
    await disclosure.focus();
    await disclosure.press("Enter");
    await capacity.getByRole("textbox").fill("1000000, 10000000, 100000000");
    await capacity.getByRole("button", { name: "시도 수 영향 확인" }).click();
    await expect(capacity).toContainText("새 시도 0");
    await capacity.getByRole("button", { name: "용량 스윕 시작" }).click();
    await expect(page).toHaveURL(/\/research\/experiments\/[^/?]+$/u);
    const experimentId = new URL(page.url()).pathname.split("/").at(-1)!;
    await expect
      .poll(
        async () =>
          requireData(
            (
              await getExperiment({
                client: apiClient,
                path: { experiment_id: experimentId },
              })
            ).data,
            "스윕 상태",
          ).settled,
        { timeout: 60_000 },
      )
      .toBe(true);
    const report = requireData(
      (
        await getExperimentCapacity({
          client: apiClient,
          path: { experiment_id: experimentId },
        })
      ).data,
      "용량 보고",
    );
    const results = page.getByRole("region", {
      name: "금액별 용량 결과",
      exact: true,
    });
    await results.getByRole("button", { name: "용량 다시 조회" }).click();
    await expect(results.getByRole("table")).toBeVisible();
    expect(report.points).toHaveLength(3);
    for (const point of report.points) {
      const row = results.getByRole("row").filter({
        has: page.getByRole("rowheader", {
          name: point.initial_cash.toLocaleString(),
          exact: true,
        }),
      });
      await expect(row).toContainText(
        point.sharpe === null ? "—" : point.sharpe.toFixed(4),
      );
    }
    const after = requireData((await ledger()).data, "실행 후 원장");
    expect(after.trial_count).toBe(before.trial_count);
    expect(
      after.trials.map((item) => [
        item.representative_run_id,
        item.representative_sharpe,
      ]),
    ).toEqual(
      before.trials.map((item) => [
        item.representative_run_id,
        item.representative_sharpe,
      ]),
    );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    const paths = results.locator(".result-chart svg path");
    await expect(paths).toHaveCount(1);
    const paint = await paths.first().evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        fill: style.fill,
        stroke: style.stroke,
        width: Number.parseFloat(style.strokeWidth),
      };
    });
    expect(paint.fill).toBe("none");
    expect(paint.stroke).not.toBe("none");
    expect(paint.stroke).not.toBe("");
    expect(paint.width).toBeGreaterThan(0);
    const notice = page.getByRole("status").filter({
      has: page.locator(`a[href="/research/experiments/${experimentId}"]`),
    });
    await expect(notice).toBeVisible();
    const noticeBox = await notice.boundingBox();
    expect(noticeBox!.x).toBeGreaterThanOrEqual(0);
    expect(noticeBox!.x + noticeBox!.width).toBeLessThanOrEqual(360);
    await notice.getByRole("button", { name: "닫기", exact: true }).click();
    await expect(notice).toHaveCount(0);
    await page.screenshot({
      path: info.outputPath("capacity-360-dark-viewport.png"),
    });
    await results.screenshot({
      path: info.outputPath("capacity-360-dark.png"),
    });
    await page.reload();
    await expect(
      page
        .getByRole("region", { name: "금액별 용량 결과", exact: true })
        .getByRole("table"),
    ).toBeVisible();
  },
);

test(
  "US-CS-12 실제 mock의 관측 부족과 별도 합성 회귀 숫자·근거를 구별해 읽는다",
  { tag: ["@story", "@US-CS-12"] },
  async ({ page }, info) => {
    const saved = requireData(
      (
        await createStrategyDocument({
          client: apiClient,
          body: {
            format: "yaml",
            source: mustReplace(
              GOLDEN,
              "퀄리티 모멘텀",
              `회귀 읽기 ${Date.now()}`,
            ),
          },
        })
      ).data,
      "회귀 기반 저장",
    );
    const runId = requireData(
      (
        await startBacktest({
          client: apiClient,
          body: {
            strategy_source: {
              kind: "saved_revision",
              strategy_id: saved.strategy_id,
              revision: saved.revision,
              expected_spec_hash: saved.spec_hash,
            },
            environment: REQUESTED_ENVIRONMENT,
          },
        })
      ).data,
      "회귀 기반 실행",
    ).run.run_id;
    await expect
      .poll(
        async () =>
          requireData(
            (
              await getBacktestStatus({
                client: apiClient,
                path: { run_id: runId },
              })
            ).data,
            "회귀 기반 상태",
          ).status,
      )
      .toBe("completed");
    await page.goto(`/research/backtests/${runId}`);
    // 팩터 회귀는 "종목 선정이 효과가 있었나" 카드 안에 있다(RD-07-C).
    const panel = page
      .getByRole("region", { name: "종목 선정이 효과가 있었나", exact: true })
      .getByRole("region", { name: "팩터 회귀", exact: true });
    await panel.getByRole("button", { name: "팩터 회귀 조회" }).click();
    await expect(panel).toContainText(
      "회귀에 필요한 유효 관측 구간이 부족합니다.",
      { timeout: 20_000 },
    );
    const result = requireData(
      (await getBacktestResult({ client: apiClient, path: { run_id: runId } }))
        .data,
      "불변 결과",
    );
    // 기본 mock의3종목을 숫자 회귀의 실제성공으로 가장하지 않는다. 이 응답은 화면 계약 전용 합성 fixture다.
    const fixture: RunFactorRegression = {
      run_id: runId,
      run_fingerprint: result.manifest.run_fingerprint,
      data_snapshot_id: "synthetic-report-fixture",
      factor_registry_version: "synthetic-registry",
      factor_evaluation_version: "synthetic-evaluator",
      momentum_factor_id: "price.momentum_12_1",
      value_factor_id: "financial.book_to_market",
      trial_count: 24,
      alpha_t_threshold: 3,
      report: {
        annualization_days: 260,
        strategy_period_count: 105,
        observation_count: 100,
        excluded_periods: [{ reason: "no_market_weight", count: 5 }],
        unavailable_reason: null,
        fit: {
          alpha_annualized: 0.12,
          alpha_per_session: 0.0004615,
          alpha_standard_error: 0.0001,
          alpha_t_value: 4.615,
          r_squared: 0.7,
          hac_lag: 4,
          exposures: [
            { factor: "market", beta: 0.8, standard_error: 0.1, t_value: 8 },
            { factor: "size", beta: 0.2, standard_error: 0.1, t_value: 2 },
            { factor: "value", beta: -0.3, standard_error: 0.1, t_value: -3 },
            { factor: "momentum", beta: 0.4, standard_error: 0.1, t_value: 4 },
          ],
        },
      },
    };
    await page.route(
      `**/api/v1/backtests/${runId}/factor-regression`,
      (route) => route.fulfill({ json: fixture }),
    );
    await panel.getByRole("button", { name: "회귀 다시 조회" }).click();
    await expect(panel).toContainText("12.00%");
    await expect(panel.getByRole("table")).toContainText("0.8000");
    const basis = panel.locator("summary");
    await basis.focus();
    await basis.press("Space");
    await expect(panel).toContainText("synthetic-report-fixture");
    await expect(panel).toContainText("시장 가중치 없음: 5");
    await panel.screenshot({
      path: info.outputPath("regression-fixture-1440.png"),
    });
  },
);
