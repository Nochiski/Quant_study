/**
 * 정동민의 스토리 e2e — AI 결과 설명이 검증 카드의 근거와 그 시점을 함께 말한다(US-DM-12).
 *
 * 공급자는 backend 대본 adapter다. 대본은 결과 요약의 `validation` 칸에서 PSR·DSR·N·조회 시점을
 * 읽어 옮기므로(`_scenarios.py`의 `_validation_parts`), 검증 카드가 읽은 서버 값과 같은 수치가 답에
 * 나오면 "서버가 카드와 같은 근거를 모델에게 줬다"를 확인한 것이다. 둘째 테스트는 실험에서 고른
 * 실행을 만든 뒤 계열 시도를 하나 더 늘려, 지금 원장 N 과 고를 당시 N 이 다른 결과에서 답이 두
 * 시점을 나눠 말하는지 본다. 실제 모델 설명의 품질은 검사하지 않는다.
 */
import { expect, test, type Page } from "@playwright/test";

import {
  createExperiment,
  createStrategyDocument,
  getBacktestStatus,
  getExperiment,
  listExperimentTrials,
  selectExperimentTrial,
  startBacktest,
  type BacktestRunSpec,
  type RunValidationReport,
} from "../../src/shared/api/generated";
import { ensureProvider } from "../assistant-helpers";
import {
  apiClient,
  backtest,
  expectPhase,
  fillRunEnvironment,
  GOLDEN,
  mustReplace,
  openEditor,
  REQUESTED_ENVIRONMENT,
  replaceSource,
  requireData,
  saveAndWaitForRevision,
} from "../workbench-helpers";

/** 대본의 퍼센트 표기(`_scenarios.py`의 `_percent`, 소수 둘째 자리)와 같은 모양. */
const percent = (value: number) => `${(value * 100).toFixed(2)}%`;

test(
  "US-DM-12 AI에게 결과를 믿어도 되는지 물으면 검증 카드의 PSR·DSR 원래 수치와 조회 시점을 함께 답한다",
  { tag: ["@story", "@US-DM-12"] },
  async ({ page }) => {
    test.setTimeout(240_000);
    await ensureProvider(page);
    await openEditor(page, "/research/strategies/new");
    await replaceSource(
      page,
      mustReplace(GOLDEN, "퀄리티 모멘텀", "US-DM-12 검증 근거 묻기"),
    );
    await expectPhase(page, "검증 통과");
    await saveAndWaitForRevision(page, 1);
    await fillRunEnvironment(page);
    const response = page.waitForResponse((value) =>
      /\/api\/v1\/backtests\/[^/]+\/validation-report$/u.test(
        new URL(value.url()).pathname,
      ),
    );
    await backtest(page).click();
    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    const http = await response;
    expect(http.ok()).toBe(true);
    const report = (await http.json()) as RunValidationReport;
    // 저장한 리비전의 첫 실행이라 계열 N=1 이고 DSR 값이 있다.
    expect(report.dsr.trial_count).toBe(1);
    expect(report.dsr.value).not.toBeNull();
    expect(report.psr?.value).toEqual(expect.any(Number));
    const card = page.getByRole("region", { name: "결과 검증", exact: true });
    await expect(card).toBeVisible();

    await page.getByRole("button", { name: "AI에게 결과 묻기" }).click();
    const panel = page.getByRole("complementary", { name: "AI 어시스턴트" });
    await panel
      .getByRole("textbox", { name: "어시스턴트에게 보낼 메시지" })
      .fill("이 결과 믿어도 돼?");
    await panel.getByRole("button", { name: "보내기" }).click();
    await expect(panel.getByRole("status", { name: "진행 상태" })).toHaveText(
      "답변이 완료되었습니다.",
      { timeout: 60_000 },
    );
    const log = panel.getByRole("log", { name: "대화 내용" });

    // 원래 수치는 검증 카드가 읽은 서버 값과 같다. PSR 은 미래 확률이 아니라고 함께 말한다.
    await expect(log).toContainText(
      `PSR(probabilistic_sharpe)은 ${percent(report.psr?.value ?? Number.NaN)}입니다.`,
    );
    await expect(log).toContainText("앞으로 벌 확률은 아닙니다");
    // DSR 은 조회 시점을 밝힌 지금 원장 기준이다(조회 시각은 AI 턴이 읽은 시각이라 카드와 다를 수 있다).
    await expect(log).toContainText(
      new RegExp(
        `지금 계열 원장\\([^)]+ 조회\\) 기준 시도 수 N=1, DSR은 ${percent(report.dsr.value ?? Number.NaN).replace(".", "\\.")}입니다\\.`,
        "u",
      ),
    );
    // 실험에서 고른 실행이 아니므로 선택 당시 기록을 지어내지 않는다.
    await expect(log).not.toContainText("후보로 고를 당시");
    await expect(log).toContainText(
      "과거 결과가 앞으로의 수익을 보장하지는 않습니다.",
    );
  },
);

/** 값 두 개(0, 1)를 펴는 파라미터 하나 — 칸마다 계열 시도가 하나씩 생긴다. */
const WITH_PARAMETER = mustReplace(
  GOLDEN,
  "parameters: []",
  [
    "parameters:",
    "  - parameter_id: scale",
    "    kind: float",
    "    default: 0.0",
    "    minimum: 0.0",
    "    maximum: 1.0",
    "    step: 1.0",
  ].join("\n"),
);

/** 학습 1년·검증 1년 롤링이면 창이 하나다. */
const ENVIRONMENT = {
  ...REQUESTED_ENVIRONMENT,
  start: "2021-01-04",
  end: "2022-12-30",
};

const ask = async (page: Page, question: string) => {
  await page.getByRole("button", { name: "AI에게 결과 묻기" }).click();
  const panel = page.getByRole("complementary", { name: "AI 어시스턴트" });
  await panel
    .getByRole("textbox", { name: "어시스턴트에게 보낼 메시지" })
    .fill(question);
  await panel.getByRole("button", { name: "보내기" }).click();
  await expect(panel.getByRole("status", { name: "진행 상태" })).toHaveText(
    "답변이 완료되었습니다.",
    { timeout: 60_000 },
  );
  return panel.getByRole("log", { name: "대화 내용" });
};

test(
  "US-DM-12 후보로 고른 뒤 계열 시도가 늘어난 결과에서 AI는 지금 원장 기준과 고를 당시 기준을 나눠 답한다",
  { tag: ["@story", "@US-DM-12"] },
  async ({ page }) => {
    test.setTimeout(300_000);
    await ensureProvider(page);
    const saved = requireData(
      (
        await createStrategyDocument({
          client: apiClient,
          body: {
            format: "yaml",
            source: mustReplace(
              WITH_PARAMETER,
              "퀄리티 모멘텀",
              `US-DM-12 선택 시점 ${Date.now()}`,
            ),
          },
        })
      ).data,
      "create strategy document",
    );
    const base: BacktestRunSpec = {
      strategy_source: {
        kind: "saved_revision",
        strategy_id: saved.strategy_id,
        revision: saved.revision,
        expected_spec_hash: saved.spec_hash,
      },
      environment: ENVIRONMENT,
    };
    const { experiment_id: experimentId } = requireData(
      (
        await createExperiment({
          client: apiClient,
          body: {
            run: base,
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
      "create experiment",
    ).record;
    // 칸 두 개(scale 0·1)의 학습과 창 검증이 모두 끝나야 후보를 고를 수 있다.
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
            "read experiment",
          ).finished,
        { timeout: 200_000, intervals: [1_000] },
      )
      .toBe(true);
    const [trial] = requireData(
      (
        await listExperimentTrials({
          client: apiClient,
          path: { experiment_id: experimentId },
        })
      ).data,
      "list experiment trials",
    );
    const selectedRun = trial?.attempts.at(-1)?.run_id;
    expect(selectedRun).toEqual(expect.any(String));
    const selection = requireData(
      (
        await selectExperimentTrial({
          client: apiClient,
          path: { experiment_id: experimentId },
          body: {
            trial_index: trial?.trial.index ?? 0,
            reason: "학습 샤프 확인",
          },
        })
      ).data,
      "select experiment trial",
    );
    // 고를 당시 계열에는 두 칸이 시도로 들어 있다.
    expect(selection.trial_count).toBe(2);

    // 같은 계열에서 새 파라미터 값으로 한 번 더 돌려 지금 원장의 N 을 3 으로 늘린다.
    const extra = requireData(
      (
        await startBacktest({
          client: apiClient,
          body: { ...base, parameter_values: { scale: 0.5 } },
        })
      ).data,
      "start extra backtest",
    ).run.run_id;
    await expect
      .poll(
        async () =>
          requireData(
            (
              await getBacktestStatus({
                client: apiClient,
                path: { run_id: extra },
              })
            ).data,
            "poll extra run",
          ).status,
        { timeout: 60_000 },
      )
      .toBe("completed");

    await page.goto(`/research/backtests/${selectedRun ?? ""}`);
    const report = page.waitForResponse((value) =>
      /\/api\/v1\/backtests\/[^/]+\/validation-report$/u.test(
        new URL(value.url()).pathname,
      ),
    );
    await expect(
      page.getByRole("region", { name: "결과 검증", exact: true }),
    ).toBeVisible();
    const current = (await (await report).json()) as RunValidationReport;
    expect(current.dsr.trial_count).toBe(3);

    const log = await ask(page, "이 후보 믿어도 돼?");
    await expect(log).toContainText(
      /지금 계열 원장\([^)]+ 조회\) 기준 (시도 수 N=3|DSR은 계산되지)/u,
    );
    await expect(log).toContainText(
      /후보로 고를 당시\([^)]+\) 기록은 시도 수 N=2,/u,
    );
    await expect(log).toContainText("지금 기준과 따로 봐야 합니다.");
  },
);
