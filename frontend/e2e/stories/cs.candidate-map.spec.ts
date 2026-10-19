import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { TRIAL_HOLD_SECONDS } from "../runtime";
import {
  createExperiment,
  createStrategyDocument,
  getExperiment,
  getExperimentParameterMap,
  listExperimentTrials,
  type CellVerdict,
  type ExperimentSelection,
} from "../../src/shared/api/generated";
import {
  apiClient,
  GOLDEN,
  mustReplace,
  REQUESTED_ENVIRONMENT,
  requireData,
} from "../workbench-helpers";

const SOURCE = mustReplace(
  mustReplace(
    mustReplace(GOLDEN, "window: 252", "window: 3"),
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
      "        - kind: parameter",
      "          node_id: offset",
      "          parameter_id: offset",
      "        - kind: binary",
      "          node_id: shifted",
      "          operator: multiply",
      "          left_node_id: scaled",
      "          right_node_id: offset",
      "      output_node_id: shifted",
    ].join("\n"),
  ),
  "parameters: []",
  [
    "parameters:",
    "  - parameter_id: scale",
    "    kind: float",
    "    default: 1.0",
    "    minimum: 0.0",
    "    maximum: 1.0",
    "    step: 1.0",
    "  - parameter_id: offset",
    "    kind: float",
    "    default: 0.0",
    "    minimum: 0.0",
    "    maximum: 1.0",
    "    step: 1.0",
  ].join("\n"),
);
const VERDICT: Record<CellVerdict, string> = {
  recommended: "추천 후보",
  peak: "봉우리",
  failed: "실패 / 파산",
  unscored: "점수 없음",
  scored: "점수 있음",
};
// 두 축을 비교하는 최소 2×2 격자와 한 창: 학습 4회 뒤 선택된 한 칸만 검증한다.
// 공용 실험 슬롯은 직렬이며 각 실행의 hold 외에 엔진·HTTP 여유 30초를 둔다.
const SETTLE_BUDGET_MS = (2 * 2 + 1) * TRIAL_HOLD_SECONDS * 1_000 + 30_000;
const SCENARIO_BUDGET_MS = SETTLE_BUDGET_MS + 30_000;

const scenario = async (
  page: Page,
  info: TestInfo,
  width: number,
  dark: boolean,
) => {
  await page.setViewportSize({ width, height: 640 });
  await page.emulateMedia({ colorScheme: dark ? "dark" : "light" });
  const savedResponse = await createStrategyDocument({
    client: apiClient,
    body: {
      format: "yaml",
      source: mustReplace(
        SOURCE,
        "퀄리티 모멘텀",
        `US-CS-10 후보 지도 ${width} ${Date.now()}`,
      ),
    },
  });
  expect(savedResponse.error, "지도 전략 저장 응답").toBeUndefined();
  const saved = requireData(
    savedResponse.data,
    "지도 전략 저장",
  );
  const created = requireData(
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
              start: "2021-01-04",
              end: "2022-12-30",
            },
          },
          search: { scale: null, offset: null },
          split: {
            mode: "rolling",
            train_years: 1,
            test_years: 1,
            embargo_sessions: 0,
          },
        },
      })
    ).data,
    "지도 실험 시작",
  );
  expect(created.record.design.windows).toHaveLength(1);
  expect(created.record.design.search.axes).toHaveLength(2);
  for (const axis of created.record.design.search.axes)
    expect(axis.values).toHaveLength(2);
  const id = created.record.experiment_id;
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
          "실험 완료",
        ).settled,
      { timeout: SETTLE_BUDGET_MS },
    )
    .toBe(true);
  const mapped = requireData(
    (
      await getExperimentParameterMap({
        client: apiClient,
        path: { experiment_id: id },
      })
    ).data,
    "서버 지도",
  );
  const states = requireData(
    (
      await listExperimentTrials({
        client: apiClient,
        path: { experiment_id: id },
      })
    ).data,
    "학습 창 목록",
  );
  const chosen = states.find(
    (state) =>
      state.selectable &&
      state.trial.parameter_values.scale === 1 &&
      state.trial.parameter_values.offset === 0,
  );
  expect(chosen).toBeDefined();
  const cell = mapped.cells.find(
    (item) =>
      JSON.stringify(item.grid_index) ===
      JSON.stringify(chosen!.trial.grid_index),
  );
  expect(cell).toBeDefined();
  const runId = chosen!.attempts.at(-1)?.run_id;
  expect(runId).toBeTruthy();
  await page.goto(`/research/experiments/${id}`);
  const map = page.getByRole("region", {
    name: "파라미터 지도와 후보 선택",
    exact: true,
  });
  const grid = map.getByRole("region", {
    name: "파라미터 조합 지도",
    exact: true,
  });
  await expect(grid.getByRole("button")).toHaveCount(mapped.cells.length);
  for (const expected of mapped.cells) {
    const value = saved.spec.parameters?.find(
      (parameter) => parameter.parameter_id === "scale",
    );
    expect(value).toBeDefined();
    const parameterValue = states.find(
      (state) =>
        JSON.stringify(state.trial.grid_index) ===
        JSON.stringify(expected.grid_index),
    )!.trial.parameter_values;
    const button = grid.getByRole("button", {
      name: new RegExp(
        `^scale ${parameterValue.scale} · offset ${parameterValue.offset} · ${VERDICT[expected.verdict]}`,
      ),
    });
    await expect(button).toHaveText(
      `${VERDICT[expected.verdict]}${expected.plateau_score === null ? "—" : expected.plateau_score.toFixed(4)}`,
    );
  }
  if (
    mapped.no_recommendation !== null &&
    mapped.no_recommendation !== undefined
  ) {
    await expect(map.getByText(/^추천할 설정이 없어요/u)).toBeVisible();
    await expect(
      grid.getByRole("button", { name: /· 추천 후보 ·/u }),
    ).toHaveCount(0);
  }
  await grid.scrollIntoViewIfNeeded();
  const horizontal = map.getByRole("combobox", { name: "가로축", exact: true });
  const vertical = map.getByRole("combobox", { name: "세로축", exact: true });
  await horizontal.selectOption("1");
  await expect(vertical).toHaveValue("0");
  await horizontal.selectOption("0");
  await expect(vertical).toHaveValue("1");
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  await page.screenshot({
    path: info.outputPath(`candidate-map-${width}.png`),
  });
  await map
    .getByRole("combobox", { name: "색으로 비교할 값", exact: true })
    .selectOption("score");
  const colorLegend = map
    .locator("summary")
    .filter({ hasText: "색 범례 · 실험 전체 상대 범위" });
  if (await colorLegend.count()) {
    await colorLegend.focus();
    await colorLegend.press("Space");
    await expect(
      map.getByText(/색은 이 실험 전체의 유효값에 대한 상대 크기입니다/u),
    ).toBeVisible();
    await page.screenshot({
      path: info.outputPath(`candidate-color-legend-${width}.png`),
    });
    await colorLegend.press("Space");
  }
  const button = grid.getByRole("button", {
    name: new RegExp(`^scale 1 · offset 0 · ${VERDICT[cell!.verdict]}`),
  });
  await button.focus();
  await button.press("Enter");
  const comparison = map.getByRole("region", {
    name: "비교 중인 칸",
    exact: true,
  });
  await expect(
    comparison.getByRole("heading", { name: "비교 중인 칸" }),
  ).toBeFocused();
  for (const [label, number] of [
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
    ).toHaveText(number === null ? "—" : String(number));
  }
  const form = comparison.getByRole("form", { name: "후보 선택 기록" });
  await form
    .getByRole("combobox", { name: "후보로 기록할 학습 창", exact: true })
    .selectOption(String(chosen!.trial.index));
  const reason = `최고 칸만 보지 않고 이웃 평균과 민감도를 비교했다. ${width}px 기술 QA이며 실제 자금 투입 판단이 아니다.`;
  await form
    .getByRole("textbox", { name: "선택 이유", exact: true })
    .fill(reason);
  await page.screenshot({
    path: info.outputPath(`candidate-reason-${width}.png`),
  });
  const response = page.waitForResponse(
    (value) =>
      new URL(value.url()).pathname ===
        `/api/v1/experiments/${id}/selections` &&
      value.request().method() === "POST",
  );
  const submit = form.getByRole("button", {
    name: "이유와 후보 기록",
    exact: true,
  });
  await submit.focus();
  await submit.press("Enter");
  const recorded = await response;
  expect(recorded.ok()).toBe(true);
  expect(recorded.request().postDataJSON()).toEqual({
    trial_index: chosen!.trial.index,
    reason,
  });
  const snapshot = (await recorded.json()) as ExperimentSelection;
  await expect(comparison.getByRole("status")).toHaveText(
    "후보 선택이 기록되었습니다.",
  );
  await expect(comparison.getByText(reason, { exact: true })).toBeVisible();
  await expect(
    comparison
      .locator("dt")
      .filter({ hasText: "선택 당시 계열 시도 수 N" })
      .locator("..")
      .locator("dd"),
  ).toHaveText(
    snapshot.trial_count == null ? "—" : String(snapshot.trial_count),
  );
  await expect(
    comparison
      .locator("dt")
      .filter({ hasText: "선택 당시 DSR (원수치)" })
      .locator("..")
      .locator("dd"),
  ).toHaveText(
    snapshot.deflated_sharpe == null ? "—" : String(snapshot.deflated_sharpe),
  );
  await page.screenshot({
    path: info.outputPath(`candidate-recorded-${width}.png`),
  });
  await comparison
    .getByRole("link", {
      name: "선택한 실행의 결과 검증 카드 보기",
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(new RegExp(`/research/backtests/${runId}$`));
  const validation = page.getByRole("region", {
    name: "운일까, 튼튼할까",
    exact: true,
  });
  await expect(validation).toContainText("계열 시도 수 N");
  await validation.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath(`candidate-validation-${width}.png`),
  });
  // 선택 당시 통계는 후속 현재 원장 조회와 별개인 서버 기록이다.
  const persisted = requireData(
    (await getExperiment({ client: apiClient, path: { experiment_id: id } }))
      .data,
    "선택 기록 재조회",
  );
  expect(persisted.selections).toContainEqual(snapshot);
};

// 실제 브라우저 실행을 위해 태그를 등록하며 성공 뒤 ROOT 게이트에서 예정 상태와 추적표를 함께 갱신한다.
test("US-CS-10 지도에서 설정을 비교하고 이유와 후보를 기록한 뒤 결과 검증 카드로 이동한다", { tag: ["@story", "@US-CS-10"] }, async ({
  page,
}, info) => {
  test.setTimeout(SCENARIO_BUDGET_MS);
  await scenario(page, info, 1440, false);
});

test("US-CS-10 좁고 낮은 다크 화면에서 키보드로 후보를 기록하고 결과 근거를 읽는다", { tag: ["@story", "@US-CS-10"] }, async ({
  page,
}, info) => {
  test.setTimeout(SCENARIO_BUDGET_MS);
  await scenario(page, info, 360, true);
});
