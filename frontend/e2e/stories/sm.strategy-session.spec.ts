/**
 * 한상목의 스토리 e2e — 전략 하나의 버전·백테스트·원장을 세션 화면에서 한 흐름으로 본다(US-SM-17).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/sm.md`다. 버전과 실행은 API 로 쌓고,
 * 세션 화면이 그 실행을 돌린 버전 아래에 묶는지, 의도·원장·버전 지문을 보이는지 본다.
 */
import { expect, test } from "@playwright/test";

import {
  createStrategyDocument,
  getBacktestStatus,
  reviseStrategyDocument,
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
  "US-SM-17 세션 화면은 버전마다 그 버전으로 돌린 백테스트를 묶고 의도·시도 원장·버전 지문을 함께 보인다",
  { tag: ["@story", "@US-SM-17"] },
  async ({ page }) => {
    test.setTimeout(180_000);
    const title = `US-SM-17 세션 ${Date.now()}`;
    const intent = "모멘텀이 품질 필터 뒤에서도 남는지 본다";
    const first = requireData(
      (
        await createStrategyDocument({
          client: apiClient,
          body: { format: "yaml", source: mustReplace(GOLDEN, "퀄리티 모멘텀", title) },
        })
      ).data,
      "create strategy document",
    );
    const second = requireData(
      (
        await reviseStrategyDocument({
          client: apiClient,
          path: { strategy_id: first.strategy_id },
          body: {
            expected_revision: first.revision,
            format: "yaml",
            source: mustReplace(
              mustReplace(GOLDEN, "퀄리티 모멘텀", title),
              'description: ""',
              `description: "${intent}"`,
            ),
          },
        })
      ).data,
      "revise strategy document",
    );
    const { run_id: runId } = requireData(
      (
        await startBacktest({
          client: apiClient,
          body: {
            strategy_source: {
              kind: "saved_revision",
              strategy_id: second.strategy_id,
              revision: second.revision,
              expected_spec_hash: second.spec_hash,
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

    await page.goto(`/research/strategies/${first.strategy_id}`);
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
    // 의도: 최신 버전 문서 속성의 설명을 그대로 다시 읽는다(RD-03-B).
    await expect(page.getByText(intent)).toBeVisible();
    await expect(page.getByText("문서 속성의 설명 · v2 기준")).toBeVisible();

    // 버전마다 그 버전으로 돌린 일: v2 아래에 완료된 백테스트와 결과 숫자, v1 은 비어 있다(RD-03-A).
    const latest = page.getByRole("article", { name: "v2" });
    await expect(latest.getByRole("link", { name: "백테스트" })).toHaveAttribute(
      "href",
      `/research/backtests/${runId}`,
    );
    await expect(latest).toContainText("완료");
    await expect(latest).toContainText("총수익률");
    await expect(latest.getByRole("link", { name: "v1 ↔ v2 비교" })).toHaveAttribute(
      "href",
      new RegExp(`/research/strategies/${first.strategy_id}/revisions/2\\?.*compare=true`),
    );
    await expect(page.getByRole("article", { name: "v1" })).toContainText(
      "이 버전으로 돌린 실행이 없어요.",
    );

    // 오른쪽: 계열 시도 수와 역할별 수, 버전 지문(RD-03-C).
    const ledger = page.getByRole("region", { name: "시도 원장", exact: true });
    await expect(ledger).toContainText("계열 시도 수 1회");
    await expect(ledger).toContainText("시도로 셈");
    const fingerprint = page.getByRole("region", { name: "버전 지문" });
    await expect(fingerprint).toContainText(second.spec_hash.slice(0, 12));
    await expect(fingerprint).toContainText("실행 설정을 바꿔도 전략 지문은 그대로예요");
    await page.getByRole("button", { name: "원장 전체 보기" }).click();
    await expect(page.getByRole("region", { name: "시도 원장 전체" })).toContainText(
      runId.slice(0, 12),
    );
  },
);
