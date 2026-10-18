/**
 * 후보 지도 실자료 opt-in 기술 QA. 기존 real-equity project의 격리 서버·DB에서만 ROOT가 실행한다.
 * 기존 split 계약의 최소 1년 학습+1년 검증을 사용하며 원천/S01/봉인/브로커를 조작하지 않는다.
 * 원수치 전달·표시·선택 기록을 대조할 뿐 성과를 판정하지 않는다.
 */
import { expect, test } from "@playwright/test";
import {
  createExperiment,
  createStrategyDocument,
  getExperiment,
  getExperimentParameterMap,
  listExperimentTrials,
  type BacktestRunResult,
  type ExperimentSelection,
  type RunValidationReport,
} from "../../src/shared/api/generated";
import {
  apiClient,
  GOLDEN,
  mustReplace,
  REQUESTED_ENVIRONMENT,
  requireData,
} from "../workbench-helpers";

const SOURCE = (
  [
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
        "    step: 2.0",
      ].join("\n"),
    ],
  ] as const
).reduce(
  (source, [before, after]) => mustReplace(source, before, after),
  GOLDEN,
);

test.describe("후보 지도 실자료 기술 QA", () => {
  test.skip(
    (process.env.E2E_REAL_EQUITY_ROOT ?? "") === "",
    "실자료 project는 E2E_REAL_EQUITY_ROOT opt-in이다",
  );
  test.setTimeout(900_000);
  test("실자료 지도에서 서버 점수와 선택 당시 기록을 읽고 해당 실행 검증 카드로 이동한다", async ({
    page,
  }, info) => {
    const saved = requireData(
      (
        await createStrategyDocument({
          client: apiClient,
          body: {
            format: "yaml",
            source: mustReplace(
              SOURCE,
              "퀄리티 모멘텀",
              `후보 지도 실자료 기술 QA ${Date.now()}`,
            ),
          },
        })
      ).data,
      "격리 후보 전략 저장",
    );
    const id = requireData(
      (
        await createExperiment({
          client: apiClient,
          body: {
            run: {
              strategy_source: {
                kind: "saved_revision",
                strategy_id: saved.strategy_id,
                revision: saved.revision,
                expected_spec_hash: saved.spec_hash,
              },
              environment: {
                ...REQUESTED_ENVIRONMENT,
                start: "2023-01-02",
                end: "2024-12-30",
              },
            },
            search: { scale: null },
            split: {
              mode: "rolling",
              train_years: 1,
              test_years: 1,
              embargo_sessions: 0,
            },
          },
        })
      ).data,
      "두 칸 실험 생성",
    ).record.experiment_id;
    await expect
      .poll(
        async () =>
          requireData(
            (
              await getExperiment({
                client: apiClient,
                path: { experiment_id: id },
              })
            ).data,
            "실험 완료 조회",
          ).settled,
        { timeout: 420_000, intervals: [2_000] },
      )
      .toBe(true);
    const [map, states] = await Promise.all([
      getExperimentParameterMap({
        client: apiClient,
        path: { experiment_id: id },
      }),
      listExperimentTrials({ client: apiClient, path: { experiment_id: id } }),
    ]);
    const mapped = requireData(map.data, "서버 지도");
    const trials = requireData(states.data, "서버 학습 창");
    expect(mapped.cells).toHaveLength(2);
    const candidate = trials.find(
      (state) => state.selectable && state.trial.parameter_values.scale === -1,
    );
    expect(candidate).toBeDefined();
    const cell = mapped.cells.find(
      (item) =>
        JSON.stringify(item.grid_index) ===
        JSON.stringify(candidate!.trial.grid_index),
    );
    expect(cell).toBeDefined();
    const runId = candidate!.attempts.at(-1)?.run_id;
    expect(runId).toBeTruthy();
    await page.goto(`/research/experiments/${id}`);
    const surface = page.getByRole("region", {
      name: "파라미터 지도와 후보 선택",
      exact: true,
    });
    await surface
      .getByRole("combobox", { name: "색으로 비교할 값", exact: true })
      .selectOption("score");
    const grid = surface.getByRole("region", {
      name: "파라미터 조합 지도",
      exact: true,
    });
    await expect(grid.getByRole("button")).toHaveCount(2);
    await grid.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath("real-candidate-map.png") });
    const target = grid.getByRole("button", { name: /^scale -1 ·/u });
    await target.focus();
    await target.press("Enter");
    const comparison = surface.getByRole("region", {
      name: "비교 중인 칸",
      exact: true,
    });
    for (const [label, value] of [
      ["칸 점수 (학습 세션 샤프)", cell!.score],
      ["고원 점수 (이웃 평균)", cell!.plateau_score],
      ["민감도 (비율 원수치)", cell!.sensitivity],
    ] as const) {
      await expect(
        comparison
          .locator("dt")
          .filter({ hasText: label })
          .locator("..")
          .locator("dd"),
      ).toHaveText(value === null ? "—" : String(value));
    }
    const reason =
      "2023~2024 격리 기술 QA: 서버의 칸·고원 점수와 민감도를 읽고 기록 경로만 확인한다. 수익성 판정이나 실제 자금 투입은 하지 않는다.";
    const form = comparison.getByRole("form", { name: "후보 선택 기록" });
    await form
      .getByRole("combobox", { name: "후보로 기록할 학습 창", exact: true })
      .selectOption(String(candidate!.trial.index));
    await form
      .getByRole("textbox", { name: "선택 이유", exact: true })
      .fill(reason);
    const accepted = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname ===
          `/api/v1/experiments/${id}/selections` &&
        response.request().method() === "POST",
    );
    await form
      .getByRole("button", { name: "이유와 후보 기록", exact: true })
      .click();
    const recorded = await accepted;
    expect(recorded.ok()).toBe(true);
    expect(recorded.request().postDataJSON()).toEqual({
      trial_index: candidate!.trial.index,
      reason,
    });
    const snapshot = (await recorded.json()) as ExperimentSelection;
    expect(snapshot.parameter_values.scale).toBe(-1);
    await expect(comparison.getByRole("status")).toHaveText(
      "후보 선택이 기록되었습니다.",
    );
    await expect(
      comparison
        .locator("dt")
        .filter({ hasText: "선택 당시 계열 시도 수 N" })
        .locator("..")
        .locator("dd"),
    ).toHaveText(
      snapshot.trial_count == null ? "—" : String(snapshot.trial_count),
    );
    await page.screenshot({
      path: info.outputPath("real-candidate-recorded.png"),
    });
    // 결과 페이지가 원래 읽는 응답을 보관하며 추가 계산이나 원천 데이터 조회를 하지 않는다.
    const resultResponse = page.waitForResponse((response) =>
      response.request().method() === "GET" &&
      new URL(response.url()).pathname === `/api/v1/backtests/${runId}/result`,
    );
    const validationResponse = page.waitForResponse((response) =>
      response.request().method() === "GET" &&
      new URL(response.url()).pathname === `/api/v1/backtests/${runId}/validation-report`,
    );
    await comparison
      .getByRole("link", {
        name: "선택한 실행의 결과 검증 카드 보기",
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(new RegExp(`/research/backtests/${runId}$`));
    const validation = page.getByRole("region", {
      name: "결과 검증",
      exact: true,
    });
    await expect(validation).toContainText("계열 시도 수 N");
    const resultHttp = await resultResponse;
    const validationHttp = await validationResponse;
    expect(resultHttp.ok()).toBe(true);
    expect(validationHttp.ok()).toBe(true);
    const result = (await resultHttp.json()) as BacktestRunResult;
    const validationReport = (await validationHttp.json()) as RunValidationReport;
    const manifest = result.manifest;
    expect(manifest.run_id).toBe(runId);
    expect(manifest.data_snapshot_id).not.toBe("");
    expect(manifest.run_fingerprint).not.toBe("");
    expect(validationReport.run_id).toBe(manifest.run_id);
    expect(validationReport.run_fingerprint).toBe(manifest.run_fingerprint);
    expect(manifest.strategy_provenance).toMatchObject({
      kind: "saved_revision",
      strategy_id: saved.strategy_id,
      revision: saved.revision,
      spec_hash: saved.spec_hash,
      source_hash: saved.source_hash,
    });
    expect(manifest.run_spec.parameter_values).toEqual(candidate!.trial.parameter_values);
    await validation.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: info.outputPath("real-candidate-validation.png"),
    });
    const persisted = requireData(
      (await getExperiment({ client: apiClient, path: { experiment_id: id } }))
        .data,
      "선택 기록 재조회",
    );
    expect(persisted.selections).toContainEqual(snapshot);
    await info.attach("technical-selection-inputs", {
      body: JSON.stringify(
        {
          experiment_id: id,
          run_id: runId,
          source_hash: saved.source_hash,
          environment: persisted.record.run.environment,
          parameter_map: mapped,
          trial_states: trials,
          manifest,
          validation_report: validationReport,
          selection: snapshot,
        },
        null,
        2,
      ),
      contentType: "application/json",
    });
  });
});
