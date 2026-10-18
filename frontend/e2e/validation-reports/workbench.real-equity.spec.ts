/** 실원천 읽기 전용 opt-in QA. 계산 검증·수익성 판정이 아닌 기존 보고서와 화면의 대조다. */
import { expect, test } from "@playwright/test";
import {
  createStrategyDocument,
  getBacktestFactorRegression,
  getBacktestResult,
  getBacktestStatus,
  getExperiment,
  getExperimentCapacity,
  getTrialLedger,
  listExperimentTrials,
  startBacktest,
  type BacktestRunSpec,
} from "../../src/shared/api/generated";
import { tCode } from "../../src/shared/config";
import {
  apiClient,
  GOLDEN,
  mustReplace,
  REQUESTED_ENVIRONMENT,
  requireData,
} from "../workbench-helpers";

const enabled = (process.env.E2E_REAL_EQUITY_ROOT ?? "") !== "";

// 기존 실자료 QA의 900초 총예산/420초 실행 예산을 재사용한다. 자동 회귀 조회는 제품의36회 한도다.
test("실원천 용량 비용과 장기 회귀의 원수치·판본을 화면에서 대조한다", async ({
  page,
}, info) => {
  test.skip(!enabled, "E2E_REAL_EQUITY_ROOT가 없는 실자료 opt-in 시나리오");
  test.setTimeout(900_000);
  const savedRun = async (
    source: string,
    start: string,
    end: string,
    timeout = 420_000,
  ) => {
    const saveResponse = await createStrategyDocument({
      client: apiClient,
      body: { format: "yaml", source },
    });
    expect(
      saveResponse.error,
      JSON.stringify(saveResponse.error),
    ).toBeUndefined();
    const saved = requireData(saveResponse.data, "실자료 후보 저장");
    const request: BacktestRunSpec = {
      strategy_source: {
        kind: "saved_revision",
        strategy_id: saved.strategy_id,
        revision: saved.revision,
        expected_spec_hash: saved.spec_hash,
      },
      environment: { ...REQUESTED_ENVIRONMENT, start, end },
    };
    const runId = requireData(
      (await startBacktest({ client: apiClient, body: request })).data,
      "실자료 실행 접수",
    ).run.run_id;
    await expect
      .poll(
        async () => {
          const state = requireData(
            (
              await getBacktestStatus({
                client: apiClient,
                path: { run_id: runId },
              })
            ).data,
            "실자료 실행 상태",
          );
          expect(state.status, JSON.stringify(state.error)).not.toBe("failed");
          return state.status;
        },
        { timeout },
      )
      .toBe("completed");
    return { runId, saved };
  };

  await test.step("짧은 실원천 실행의 금액별 비용·대표값 보존과 재확인 기록", async () => {
    const source = mustReplace(
      mustReplace(
        mustReplace(GOLDEN, "퀄리티 모멘텀", `실원천 용량 ${Date.now()}`),
        "rebalance: monthly",
        "rebalance: every_n_sessions\n  rebalance_every_n_sessions: 1",
      ),
      "window: 252",
      "window: 3",
    );
    const { runId, saved } = await savedRun(
      source,
      "2024-01-08",
      "2024-01-12",
      60_000,
    );
    const before = requireData(
      (
        await getTrialLedger({
          client: apiClient,
          path: { strategy_id: saved.strategy_id },
        })
      ).data,
      "용량 전 원장",
    );
    expect(before.trials).toHaveLength(1);
    expect(before.trials[0]!.representative_run_id).toBe(runId);
    expect(before.trials[0]!.runs).toHaveLength(1);
    expect(before.trials[0]!.runs[0]!.role).toBe("counted");
    expect(before.trials[0]!.representative_sharpe).toBe(
      before.trials[0]!.runs[0]!.session_sharpe,
    );
    await page.goto(`/research/backtests/${runId}`);
    const panel = page.getByRole("region", { name: "용량 확인", exact: true });
    await panel.locator("summary").click();
    await panel.getByRole("textbox").fill("1000000, 10000000");
    await panel.getByRole("button", { name: "시도 수 영향 확인" }).click();
    // 금액 두 개는 기존 API의 최소3개 계약에 미달한다. 기준을 낮추지 않고 서버 거절부터 확인한다.
    await expect(panel.getByRole("alert")).toContainText("3");
    await panel.getByRole("textbox").fill("1000000, 10000000, 100000000");
    await panel.getByRole("button", { name: "시도 수 영향 확인" }).click();
    await expect(panel).toContainText("새 시도 0");
    await panel.getByRole("button", { name: "용량 스윕 시작" }).click();
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
            "용량 상태",
          ).settled,
        { timeout: 120_000 },
      )
      .toBe(true);
    const report = requireData(
      (
        await getExperimentCapacity({
          client: apiClient,
          path: { experiment_id: experimentId },
        })
      ).data,
      "실원천 용량 보고",
    );
    expect(report.points).toHaveLength(3);
    const results = page.getByRole("region", {
      name: "금액별 용량 결과",
      exact: true,
    });
    await results.getByRole("button", { name: "용량 다시 조회" }).click();
    for (const point of report.points) {
      expect(point.status).toBe("completed");
      for (const value of [
        point.participation_rate,
        point.impact_cost_bps,
        point.rounding_error,
        point.session_unfilled_ratio,
      ])
        expect(value).not.toBeNull();
      const row = results.getByRole("row").filter({
        has: page.getByRole("rowheader", {
          name: point.initial_cash.toLocaleString(),
          exact: true,
        }),
      });
      for (const value of [
        point.participation_rate,
        point.rounding_error,
        point.session_unfilled_ratio,
      ])
        await expect(row).toContainText(`${(value! * 100).toFixed(2)}%`);
      await expect(row).toContainText(point.impact_cost_bps!.toFixed(4));
      await expect(row).toContainText(
        point.sharpe === null ? "—" : point.sharpe.toFixed(4),
      );
    }
    const after = requireData(
      (
        await getTrialLedger({
          client: apiClient,
          path: { strategy_id: saved.strategy_id },
        })
      ).data,
      "용량 후 원장",
    );
    expect(after.trial_count).toBe(before.trial_count);
    expect(after.lineage_id).toBe(before.lineage_id);
    expect(after.merged_lineage_ids).toEqual(before.merged_lineage_ids);
    expect(after.blocked).toEqual(before.blocked);
    expect(after.trials.map((trial) => trial.trial_key)).toEqual(
      before.trials.map((trial) => trial.trial_key),
    );
    const attempts = requireData(
      (
        await listExperimentTrials({
          client: apiClient,
          path: { experiment_id: experimentId },
        })
      ).data,
      "용량 재확인 실행 목록",
    );
    expect(attempts).toHaveLength(3);
    const added = after.trials.flatMap((trial, index) => {
      const previous = before.trials[index]!;
      expect(trial.representative_run_id).toBe(previous.representative_run_id);
      expect(trial.representative_sharpe).toBe(previous.representative_sharpe);
      expect(trial.metric_registry_version).toBe(
        previous.metric_registry_version,
      );
      // 기존 실행의 모든 필드와 순서는 그대로이고 새 실행만 뒤에 추가된다.
      expect(trial.runs.slice(0, previous.runs.length)).toEqual(previous.runs);
      return trial.runs.slice(previous.runs.length);
    });
    expect(added).toHaveLength(3);
    expect(added.map((run) => run.run_id).sort()).toEqual(
      attempts
        .flatMap((trial) => trial.attempts.map((attempt) => attempt.run_id))
        .sort(),
    );
    for (const run of added) {
      expect(run.role).toBe("recheck");
      expect(run.status).toBe("completed");
      const trial = attempts.find((item) =>
        item.attempts.some((attempt) => attempt.run_id === run.run_id),
      )!;
      expect(trial.attempts).toHaveLength(1);
      expect(run.session_sharpe).toBe(
        report.points.find((point) => point.trial_index === trial.trial.index)!
          .sharpe,
      );
    }
    await results.screenshot({
      path: info.outputPath("real-capacity-report.png"),
    });
    await info.attach("real-capacity-raw", {
      body: JSON.stringify(report, null, 2),
      contentType: "application/json",
    });
  });

  await test.step("252세션 모멘텀 장기 실행에서 명시 회귀 조회와 판본 대조", async () => {
    const { runId, saved } = await savedRun(
      mustReplace(GOLDEN, "퀄리티 모멘텀", `실원천 회귀 ${Date.now()}`),
      "2020-01-02",
      "2024-12-30",
    );
    const result = requireData(
      (await getBacktestResult({ client: apiClient, path: { run_id: runId } }))
        .data,
      "장기 불변 결과",
    );
    const ledgerBeforeRegression = requireData(
      (
        await getTrialLedger({
          client: apiClient,
          path: { strategy_id: saved.strategy_id },
        })
      ).data,
      "회귀 조회 전 원장",
    );
    await page.goto(`/research/backtests/${runId}`);
    const panel = page.getByRole("region", { name: "팩터 회귀", exact: true });
    const first = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/backtests/${runId}/factor-regression`) &&
        response.request().method() === "GET",
    );
    await panel.getByRole("button", { name: "팩터 회귀 조회" }).click();
    expect((await (await first).json()).report.unavailable_reason).toBe(
      "computing",
    );
    await expect(panel).toContainText("계산을 준비 중이거나 진행 중");
    await expect(panel.getByRole("table")).toBeVisible({ timeout: 180_000 });
    const raw = requireData(
      (
        await getBacktestFactorRegression({
          client: apiClient,
          path: { run_id: runId },
        })
      ).data,
      "실원천 회귀 보고",
    );
    expect(raw.run_fingerprint).toBe(result.manifest.run_fingerprint);
    expect(raw.report.unavailable_reason).toBeNull();
    expect(raw.report.fit).not.toBeNull();
    const fit = raw.report.fit!;
    await expect(panel).toContainText(
      `${(fit.alpha_annualized * 100).toFixed(2)}%`,
    );
    for (const row of fit.exposures)
      for (const value of [row.beta, row.t_value, row.standard_error])
        await expect(panel.getByRole("table")).toContainText(
          value === null ? "—" : value.toFixed(4),
        );
    await panel.locator("summary").click();
    await expect(panel).toContainText(raw.data_snapshot_id);
    await expect(panel).toContainText(raw.factor_registry_version);
    expect(raw.factor_evaluation_version).not.toBeNull();
    await expect(panel).toContainText(raw.factor_evaluation_version!);
    await expect(panel).toContainText(String(raw.report.observation_count));
    for (const gap of raw.report.excluded_periods)
      await expect(panel).toContainText(
        `${tCode(`backtest.regression.gap.${gap.reason}`, gap.reason)}: ${gap.count}`,
      );
    await expect(panel).toContainText(String(fit.alpha_per_session));
    await expect(panel).toContainText(String(fit.alpha_standard_error));
    const ledgerAfterRegression = requireData(
      (
        await getTrialLedger({
          client: apiClient,
          path: { strategy_id: saved.strategy_id },
        })
      ).data,
      "회귀 조회 후 원장",
    );
    expect(ledgerAfterRegression).toEqual(ledgerBeforeRegression);
    await panel.screenshot({
      path: info.outputPath("real-factor-regression.png"),
    });
    await info.attach("real-regression-raw", {
      body: JSON.stringify(raw, null, 2),
      contentType: "application/json",
    });
  });
});
