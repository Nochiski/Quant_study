/**
 * ROOT만 실행하는 기존 real-equity opt-in 기술 QA다. 2024-01-08~12 외 측정·실험 접수는 하지 않는다.
 * 선택값 전달과 원본 요청·계열 N 불변만 확인하며 실수익성·실계좌·봉인 검증으로 해석하지 않는다.
 */
import { expect, test } from "@playwright/test";
import {
  compileStrategyDocument,
  createStrategyDocument,
  getBacktestRequest,
  getBacktestStatus,
  getTrialLedger,
  startBacktest,
  type ExperimentRequest,
} from "../../src/shared/api/generated";
import {
  apiClient,
  fillRunEnvironment,
  GOLDEN,
  mustReplace,
  REQUESTED_ENVIRONMENT,
  requireData,
} from "../workbench-helpers";

const ENVIRONMENT = {
  start: "2024-01-08",
  end: "2024-01-12",
  universe_id: "krx.common-stock",
};
// 기존 성공한 parameters/workbench.real-equity.spec.ts의 3세션 모멘텀×scale 입력과 동일하다.
// 매일은 새 enum이 아니라 기존 every_n_sessions + 1로 명시한다.
const SOURCE = [
  ["field_id: price.adj_close", "field_id: price.close"],
  ["window: 252", "window: 3"],
  ["selection_count: 20", "selection_count: 1"],
  [
    "rebalance: monthly",
    "rebalance: every_n_sessions\n  rebalance_every_n_sessions: 1",
  ],
  ["max_name_weight: 0.05", "max_name_weight: 1.0"],
  [
    "      output_node_id: mom_252",
    [
      "        - kind: parameter",
      "          node_id: scale",
      "          parameter_id: scale",
      "        - kind: binary",
      "          node_id: scaled",
      "          operator: multiply",
      "          left_node_id: mom_252",
      "          right_node_id: scale",
      "      output_node_id: scaled",
    ].join("\n"),
  ],
  [
    "parameters: []",
    [
      "parameters:",
      "  - parameter_id: scale",
      "    kind: float",
      "    default: 1.0",
      "    minimum: -1.0",
      "    maximum: 1.0",
    ].join("\n"),
  ],
].reduce(
  (source, [before, after]) => mustReplace(source, before!, after!),
  GOLDEN,
);

test.describe("IDE 연구 진입점 실자료 기술 QA", () => {
  test.skip(
    (process.env.E2E_REAL_EQUITY_ROOT ?? "") === "",
    "실자료 root가 없는 opt-in 환경이다",
  );
  test.setTimeout(900_000);
  test("짧은 실행의 선택값0과 현재환경을 실험 미리 계산에 보내고 원본과 N을 보존한다", async ({
    page,
  }, info) => {
    const source = mustReplace(SOURCE, "퀄리티 모멘텀", "연구");
    const compiled = requireData(
      (
        await compileStrategyDocument({
          client: apiClient,
          body: { format: "yaml", source },
        })
      ).data,
      "기술 QA 입력 compile",
    );
    expect(
      compiled.diagnostics.filter((item) => item.severity === "error"),
    ).toEqual([]);
    expect(compiled.spec).not.toBeNull();
    const saved = requireData(
      (
        await createStrategyDocument({
          client: apiClient,
          body: { format: "yaml", source },
        })
      ).data,
      "기술 QA 전략 저장",
    );
    expect(saved.spec_hash).toBe(compiled.spec_hash);
    const reference = {
      kind: "saved_revision" as const,
      strategy_id: saved.strategy_id,
      revision: saved.revision,
      expected_spec_hash: saved.spec_hash,
    };
    const runId = requireData(
      (
        await startBacktest({
          client: apiClient,
          body: {
            strategy_source: reference,
            environment: ENVIRONMENT,
            parameter_values: { scale: 0 },
          },
        })
      ).data,
      "짧은 실행 접수",
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
            "짧은 실행 완료 조회",
          );
          expect(state.error, JSON.stringify(state.error)).toBeNull();
          return state.status;
        },
        { timeout: 420_000, intervals: [2_000] },
      )
      .toBe("completed");
    const original = requireData(
      (await getBacktestRequest({ client: apiClient, path: { run_id: runId } }))
        .data,
      "원본 실행 요청",
    );
    const before = requireData(
      (
        await getTrialLedger({
          client: apiClient,
          path: { strategy_id: saved.strategy_id },
        })
      ).data,
      "미리 계산 전 원장",
    );
    expect(original.strategy_source).toEqual(reference);
    expect(original.parameter_values).toEqual({ scale: 0 });
    let submissions = 0;
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        new URL(request.url()).pathname === "/api/v1/experiments"
      )
        submissions++;
    });
    await page.goto(
      `/research/strategies/${saved.strategy_id}/revisions/${saved.revision}?run=${runId}&view=yaml`,
    );
    await fillRunEnvironment(page, {}, ENVIRONMENT);
    const selection = page.getByRole("region", {
      name: "선택한 실행의 파라미터",
      exact: true,
    });
    await selection.locator("summary").click();
    await expect(selection).toContainText(runId);
    await expect(
      selection
        .locator("dt", { hasText: /^scale$/u })
        .locator("..")
        .locator("dd"),
    ).toHaveText("0");
    await selection.locator("summary").click();
    const menu = page.locator("summary").filter({ hasText: /^연구/u });
    await expect(menu).toContainText(`N ${before.trial_count}`);
    await menu.click();
    await expect(
      page.getByText(
        "선택한 실행의 파라미터 값을 사용합니다. 과거 실행 전체를 재현하지 않습니다.",
      ),
    ).toBeVisible();
    await page.screenshot({
      path: info.outputPath("real-ide-research-menu.png"),
    });
    const response = page.waitForResponse(
      (received) =>
        received.request().method() === "POST" &&
        new URL(received.url()).pathname === "/api/v1/experiments/preview",
    );
    await page
      .getByRole("button", { name: "실험으로 보내기", exact: true })
      .click();
    const received = await response;
    const request = received.request().postDataJSON() as ExperimentRequest;
    expect(request.run.strategy_source).toEqual(reference);
    expect(request.run.parameter_values).toEqual({ scale: 0 });
    expect(request.run.environment).toEqual({
      ...REQUESTED_ENVIRONMENT,
      ...ENVIRONMENT,
    });
    // 1주 기간에는 기존 3년 학습·1년 검증 창이 없다. 범위를 늘리거나 실제 실험을 접수하지 않는다.
    expect(received.status()).toBe(422);
    expect(await received.json()).toMatchObject({
      detail: { code: "experiment.split.no_window" },
    });
    await expect(page.getByRole("alert")).toContainText(
      "실행 기간이 학습 기간보다 짧아 검증할 창이 없습니다.",
    );
    await page.screenshot({
      path: info.outputPath("real-ide-experiment-preview-rejected.png"),
    });
    expect(submissions).toBe(0);
    expect(
      requireData(
        (
          await getBacktestRequest({
            client: apiClient,
            path: { run_id: runId },
          })
        ).data,
        "확인 뒤 원본 요청",
      ),
    ).toEqual(original);
    expect(
      requireData(
        (
          await getTrialLedger({
            client: apiClient,
            path: { strategy_id: saved.strategy_id },
          })
        ).data,
        "확인 뒤 원장",
      ),
    ).toEqual(before);
  });
});
