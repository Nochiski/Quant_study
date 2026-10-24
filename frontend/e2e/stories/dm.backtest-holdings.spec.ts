/**
 * 정동민의 스토리 e2e — 백테스트 결과에서 무엇을 샀고 무엇을 들고 있었는지 본다(US-DM-16).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다. 보유·매매 구분·비중·손익은
 * backend 투영이 정하고, 이 파일은 실제 mock backend 응답과 화면 표시를 대조한다. 값을 화면 테스트에서 다시
 * 계산하지 않는다.
 */
import { expect, test } from "@playwright/test";

import {
  createStrategyDocument,
  getBacktestHoldings,
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
  "US-DM-16 결과 화면에서 매매한 날을 골라 그날 들고 있던 종목을 보고, 종목별 체결 기록을 연다",
  { tag: ["@story", "@US-DM-16"] },
  async ({ page }, testInfo) => {
    test.setTimeout(180_000);
    const saved = requireData(
      (
        await createStrategyDocument({
          client: apiClient,
          body: {
            format: "yaml",
            source: mustReplace(GOLDEN, "퀄리티 모멘텀", `US-DM-16 보유 ${Date.now()}`),
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
            environment: REQUESTED_ENVIRONMENT,
          },
        })
      ).data,
      "start backtest",
    ).run.run_id;
    await expect
      .poll(
        async () =>
          requireData(
            (await getBacktestStatus({ client: apiClient, path: { run_id: runId } })).data,
            "poll backtest",
          ).status,
        { timeout: 60_000 },
      )
      .toBe("completed");
    const overview = requireData(
      (await getBacktestHoldings({ client: apiClient, path: { run_id: runId } })).data,
      "holdings overview",
    );
    expect(overview.trade_sessions.length).toBeGreaterThan(1);
    const latest = overview.trade_sessions.at(-1)!;
    const earlier = overview.trade_sessions[0];

    await page.goto(`/research/backtests/${runId}`);
    const result = page.getByRole("article", { name: "백테스트 결과" });
    const link = result
      .getByRole("navigation", { name: "결과 둘러보기" })
      .getByRole("link", { name: "보유 종목과 매매", exact: true });
    await link.focus();
    await link.press("Enter");
    const holdings = result.getByRole("region", { name: "보유 종목과 매매", exact: true });
    await expect(holdings).toBeFocused();

    // 처음에는 마지막 매매일의 장 마감 보유가 보이고, 서버가 센 종목 수와 같다.
    await expect(
      holdings.getByRole("heading", {
        name: `${latest.session} 장 마감 보유 ${latest.holding_count}종목`,
      }),
    ).toBeVisible();
    const table = holdings.getByRole("region", { name: "보유 종목 표" });
    await expect(table.locator("tbody tr")).toHaveCount(latest.holding_count);
    // 종목은 코드가 아니라 디렉터리 이름으로 보인다.
    const named = overview.securities.find((item) => item.security.name !== null)!;
    await expect(holdings).toContainText(named.security.name!);
    await page.screenshot({ path: testInfo.outputPath("holdings-sessions.png"), fullPage: false });

    // 목록에서 앞 매매일을 고르면 그날 보유로 바뀐다.
    const dates = holdings.getByRole("group", { name: /매매한 날/ });
    // 목록은 마지막 매매일에서 열리고, 칸을 넘는 날짜는 세로로 스크롤해 찾는다.
    await dates.evaluate((element) => {
      element.scrollTop = 0;
    });
    await dates.getByRole("button", { name: new RegExp(earlier.session) }).click();
    await expect(
      holdings.getByRole("heading", {
        name: `${earlier.session} 장 마감 보유 ${earlier.holding_count}종목`,
      }),
    ).toBeVisible();

    // 날짜 칸에 키보드로 연·월·일을 한 자리씩 쳐도(덜 찬 값에서 되돌아가지 않고) 그날 보유가 열린다.
    const second = overview.trade_sessions[1];
    const dayInput = holdings.getByLabel("아무 날의 보유 보기");
    await dayInput.focus();
    await page.keyboard.type(second.session.replaceAll("-", ""));
    await expect(dayInput).toHaveValue(second.session);
    await expect(
      holdings.getByRole("heading", {
        name: `${second.session} 장 마감 보유 ${second.holding_count}종목`,
      }),
    ).toBeVisible();

    // 종목별 보기에서 종목을 고르면 그 종목의 체결 기록이 열린다.
    await holdings.getByRole("tab", { name: "종목별 매매 내역" }).click();
    const first = overview.securities[0];
    await holdings
      .getByRole("button", { name: new RegExp(`체결 ${first.fill_count}건`) })
      .first()
      .click();
    const fills = holdings.getByRole("region", { name: "체결 기록 표" });
    await expect(fills.locator("tbody tr")).toHaveCount(first.fill_count);
    await page.screenshot({ path: testInfo.outputPath("holdings-securities.png"), fullPage: false });

    // 360px 화면에서도 구역이 가로로 넘치지 않는다(넓은 표는 이름 있는 칸 안에서 스크롤한다).
    await page.setViewportSize({ width: 360, height: 800 });
    await holdings.getByRole("tab", { name: "매매한 날별 보유" }).click();
    await expect(holdings.getByRole("region", { name: "보유 종목 표" })).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  },
);
