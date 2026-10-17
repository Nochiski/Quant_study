/**
 * 정동민·김철수의 스토리 e2e — 한 번 돌린 결과에서 버튼 하나로 튼튼한지 확인하고(US-DM-11), 그 워크포워드의
 * 표본 밖 곡선과 유지율을 결과 검증 카드에서 본다(US-CS-09).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`·`cs.md`다. 확인 실험의 설계
 * (팩터 출력이 읽는 파라미터의 지금 값 주변 격자, 롤링 학습 3년·검증 1년·엠바고 5세션)·시도 수·창 선택·고원 판정은 backend 가
 * 정한다. mock 실행은 빨리 끝나지만 실험 trial 은 backend 훅(`TRIAL_HOLD_SECONDS`)이 붙잡아, 시작 직후
 * "도는 중" 상태를 화면에서 본다.
 */
import { expect, test } from "@playwright/test";

import {
  createStrategyDocument,
  getBacktestStatus,
  getExperimentWalkForward,
  listRobustnessChecks,
  startBacktest,
} from "../../src/shared/api/generated";
import {
  apiClient,
  GOLDEN,
  mustReplace,
  REQUESTED_ENVIRONMENT,
  requireData,
} from "../workbench-helpers";

/**
 * 값 두 개(0, 1)를 펴는 파라미터 하나 — 고원 지도의 칸이 둘이다. 확인 실험은 팩터 출력이 읽는
 * 파라미터만 펴므로 모멘텀에 배율 `scale`(기본 1)을 곱한다.
 */
const WITH_PARAMETER = mustReplace(
  mustReplace(
    GOLDEN,
    "      output_node_id: mom_252",
    [
      "        - kind: parameter",
      "          node_id: scale_value",
      "          parameter_id: scale",
      "        - kind: binary",
      "          node_id: scaled",
      "          operator: multiply",
      "          left_node_id: mom_252",
      "          right_node_id: scale_value",
      "      output_node_id: scaled",
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
  ].join("\n"),
);

/** 거래일 기준 꽉 찬 4년: 학습 3년 + 검증 1년이 들어가 창이 하나다(실행 세 개: 칸 둘 + 검증 하나). */
const ENVIRONMENT = {
  ...REQUESTED_ENVIRONMENT,
  start: "2020-01-02",
  end: "2023-12-28",
};

test(
  "US-DM-11 US-CS-09 한 번 돌린 결과에서 튼튼한지 확인하기로 시도 수를 먼저 보고 실험을 걸면, 끝난 뒤 표본 밖 곡선·유지율과 추천 후보가 카드에 채워진다",
  { tag: ["@story", "@US-DM-11", "@US-CS-09"] },
  async ({ page }) => {
    test.setTimeout(300_000);
    const saved = requireData(
      (
        await createStrategyDocument({
          client: apiClient,
          body: {
            format: "yaml",
            source: mustReplace(
              WITH_PARAMETER,
              "퀄리티 모멘텀",
              `US-DM-11 튼튼한지 ${Date.now()}`,
            ),
          },
        })
      ).data,
      "create strategy document",
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
            environment: ENVIRONMENT,
          },
        })
      ).data,
      "start backtest",
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
            "poll backtest",
          ).status,
        { timeout: 60_000 },
      )
      .toBe("completed");

    await page.goto(`/research/backtests/${runId}`);
    const card = page.getByRole("region", { name: "결과 검증", exact: true });
    const outOfSample = card.getByRole("region", {
      name: "표본 밖 성과 · 워크포워드",
    });
    const plateau = card.getByRole("region", { name: "파라미터 고원" });
    // 실험 없이 한 번 돌린 결과: 두 항목이 실험이 필요하다고 말하고 버튼이 있다.
    await expect(outOfSample).toContainText("실험이 필요해요");
    await expect(plateau).toContainText("실험이 필요해요");

    await card.getByRole("button", { name: "튼튼한지 확인하기" }).click();
    const dialog = page.getByRole("dialog", { name: "튼튼한지 확인하기" });
    // 새로 세는 시도 수가 먼저 보인다. 기반 실행(scale 1)은 이미 센 시도라 scale 0 하나만 새로 센다.
    const trials = dialog.locator("p").first();
    await expect(trials).toHaveText("새로 세는 시도 1개 · 계열 시도 수 1 → 2");
    await expect(dialog).toContainText(
      "지금 값과 양옆을 볼 파라미터: scale 0, 1",
    );
    // 창 미리보기의 어떤 창도 봉인 구간(2019-12-31 이전)에 걸리지 않는다.
    const windows = dialog.getByRole("table", { name: "학습·검증 창" });
    const rows = windows.locator("tbody tr");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("2020-01-02 ~");
    await expect(rows.first()).toContainText("2023-01-");
    for (const text of await windows.locator("tbody td").allInnerTexts()) {
      for (const day of text.match(/\d{4}-\d{2}-\d{2}/gu) ?? []) {
        expect(day >= "2020-01-02").toBe(true);
      }
    }
    await dialog.getByRole("button", { name: "시작" }).click();
    await expect(dialog).toBeHidden();
    // 실험이 대기열에 들어가 두 항목이 "도는 중"을 말한다.
    await expect(outOfSample).toContainText(
      "확인 실험이 아직 끝나지 않았어요",
      {
        timeout: 30_000,
      },
    );

    // 실험이 끝나면 두 항목이 채워진다. 표본 밖 샤프·유지율은 서버 워크포워드 보고의 값 그대로다.
    await expect(plateau).toContainText("추천 후보", { timeout: 240_000 });
    const [check] = requireData(
      (
        await listRobustnessChecks({
          client: apiClient,
          path: { run_id: runId },
        })
      ).data,
      "list robustness checks",
    );
    const report = requireData(
      (
        await getExperimentWalkForward({
          client: apiClient,
          path: { experiment_id: check?.record.experiment_id ?? "" },
        })
      ).data,
      "read walk-forward report",
    );
    // 모든 창의 검증이 끝나 곡선과 표본 밖 샤프가 있다. 유지율은 학습 샤프 평균이 0 이하면 서버가 비우고
    // 이유를 싣는다(mock 자료가 그렇다) — 화면은 그 값과 이유를 그대로 보인다.
    expect(report.gap ?? null).toBeNull();
    expect(report.out_of_sample_sharpe).toEqual(expect.any(Number));
    const retention =
      report.retention === null
        ? "—"
        : `${(report.retention * 100).toFixed(1)}%`;
    await expect(outOfSample).toContainText(
      `표본 밖 샤프(세션 단위) ${report.out_of_sample_sharpe?.toFixed(4) ?? ""} · 유지율 ${retention}`,
    );
    // mock 자료는 학습 샤프 평균이 0 이하라 유지율이 빈다. "—"만 두지 않고 서버가 준 이유를 말한다.
    expect(report.retention).toBeNull();
    expect(report.retention_gap).toBe("non_positive_train_sharpe");
    await expect(outOfSample).toContainText(
      "유지율 없음: 고른 설정의 학습 샤프 평균이",
    );
    await expect(
      outOfSample.getByRole("img", { name: /검증 구간만 이어 붙인 곡선/u }),
    ).toBeVisible();
    await expect(plateau).toContainText(/추천 후보: scale [01]/u);
    await expect(
      card.getByRole("button", { name: "튼튼한지 확인하기" }),
    ).toHaveCount(0);

    // 실험 화면에는 창 목록과 창마다 고른 파라미터가 보인다(US-CS-09).
    await card.getByRole("link", { name: "확인 실험 자세히 보기" }).click();
    const walkForward = page.getByRole("region", {
      name: "워크포워드 검증 창",
    });
    await expect(walkForward.locator("tbody tr")).toHaveCount(1);
    await expect(walkForward.locator("tbody tr").first()).toContainText(
      /scale [01]/u,
    );
  },
);
