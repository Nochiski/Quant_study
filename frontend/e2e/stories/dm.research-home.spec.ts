/**
 * 정동민의 스토리 e2e — 앱을 열면 이어서 할 일과 돌고 있는 일을 먼저 본다(US-DM-15).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다. 실행은 API 로 쌓고, 첫 화면이
 * 그 결과를 최근 결과·작업 기록에 보이는지, 계좌 칸이 숫자 없이 미연결만 말하는지 본다.
 */
import { expect, test } from "@playwright/test";

import {
  createStrategyDocument,
  getBacktestStatus,
  startBacktest,
} from "../../src/shared/api/generated";
import {
  apiClient,
  GOLDEN,
  mustReplace,
  REQUESTED_ENVIRONMENT,
  requireData,
} from "../workbench-helpers";

test(
  "US-DM-15 연구 홈은 최근 결과와 작업 기록을 보이고 계좌 칸에는 숫자 없이 미연결만 말한다",
  { tag: ["@story", "@US-DM-15"] },
  async ({ page }) => {
    test.setTimeout(180_000);
    const title = `US-DM-15 홈 ${Date.now()}`;
    const saved = requireData(
      (
        await createStrategyDocument({
          client: apiClient,
          body: { format: "yaml", source: mustReplace(GOLDEN, "퀄리티 모멘텀", title) },
        })
      ).data,
      "create strategy document",
    );
    const { run_id: runId } = requireData(
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
      "start backtest",
    ).run;
    await expect
      .poll(
        async () =>
          requireData(
            (await getBacktestStatus({ client: apiClient, path: { run_id: runId } })).data,
            "poll backtest run state",
          ).status,
        { timeout: 120_000, intervals: [1_000] },
      )
      .toBe("completed");

    await page.goto("/");
    await expect(page).toHaveURL(/\/$/u);
    await expect(page.getByRole("heading", { level: 1, name: "연구 홈" })).toBeVisible();
    // 이름 일부 일치면 이 테스트가 저장한 전략 링크(`US-DM-15 홈 …`)도 잡혀, 최근 작업 목록이
    // 먼저 그려진 회차에 strict mode 로 실패한다. 내비게이션 링크만 정확히 고른다.
    await expect(page.getByRole("link", { name: "홈", exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );

    // 계좌 칸: 미연결 배지와 문장뿐이고 숫자가 없다(RD-01-B).
    const account = page.getByRole("region", { name: /계좌 손익/u });
    await expect(account).toContainText("미연결");
    await expect(account).toContainText("지금 보이는 손익은 모두 백테스트·모의 계산입니다");
    expect(await account.innerText()).not.toMatch(/\d/u);

    // 할 일: 있든 없든 제목이 그 사실을 말한다(RD-01-A).
    await expect(
      page.getByRole("region", { name: /오늘 이어서 할 일|이어서 할 일이 없어요/u }),
    ).toBeVisible();

    // 방금 끝난 실행이 최근 결과와 작업 기록에 보인다(RD-01-C·D).
    const results = page.getByRole("region", { name: "최근 결과" });
    const row = results.getByRole("row").filter({ hasText: `${saved.strategy_id} · v1` });
    await expect(row).toContainText("총수익률");
    await expect(row.getByRole("link", { name: "결과 열기" })).toHaveAttribute(
      "href",
      `/research/backtests/${runId}`,
    );
    const activity = page.getByRole("region", { name: "작업 기록" });
    await expect(activity).toContainText("주문·체결 운영 로그가 아닙니다");
    await expect(activity).toContainText(`백테스트 완료 · ${saved.strategy_id} · v1`);
  },
);
