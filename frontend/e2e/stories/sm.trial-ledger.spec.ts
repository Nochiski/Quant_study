/**
 * 한상목의 스토리 e2e — 시도 수가 자동으로 쌓이고 지울 수 없다(US-SM-12).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/sm.md`다. 시도 묶음·재확인·N 제외는
 * backend 원장이 정한다(검증 랩 spec D2). 실행은 API 로 쌓고, 전략 이력의 "시도 원장" 탭이 그 판정을 이름으로
 * 보이는지와 지우기·나누기 없이 "다른 계열과 합치기"만 있는지를 본다.
 */
import { expect, test, type Page } from "@playwright/test";

import {
  createStrategyDocument,
  getBacktestStatus,
  reviseStrategyDocument,
  startBacktest,
  type BacktestRunSpec,
  type StrategyDocument,
} from "../../src/shared/api/generated";
import {
  apiClient,
  GOLDEN,
  mustReplace,
  REQUESTED_ENVIRONMENT,
  requireData,
} from "../workbench-helpers";

const revise = async (
  saved: StrategyDocument,
  source: string,
): Promise<StrategyDocument> =>
  requireData(
    (
      await reviseStrategyDocument({
        client: apiClient,
        path: { strategy_id: saved.strategy_id },
        body: { expected_revision: saved.revision, format: "yaml", source },
      })
    ).data,
    "revise strategy document",
  );

const request = (
  saved: StrategyDocument,
  overrides: Partial<BacktestRunSpec> = {},
): BacktestRunSpec => ({
  strategy_source: {
    kind: "saved_revision",
    strategy_id: saved.strategy_id,
    revision: saved.revision,
    expected_spec_hash: saved.spec_hash,
  },
  environment: REQUESTED_ENVIRONMENT,
  ...overrides,
});

/** 실행을 접수하고 완료까지 기다린다. */
const completedRun = async (spec: BacktestRunSpec): Promise<string> => {
  const { run_id: runId } = requireData(
    (await startBacktest({ client: apiClient, body: spec })).data,
    "start backtest",
  ).run;
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
          "poll backtest run state",
        ).status,
      { timeout: 120_000, intervals: [1_000] },
    )
    .toBe("completed");
  return runId;
};

const runEntry = (page: Page, runId: string) =>
  page
    .getByRole("listitem")
    .filter({ has: page.getByRole("link", { name: runId.slice(0, 12) }) });

test(
  "US-SM-12 시도 원장은 설정을 바꾼 실행만 새 시도로 세고 재확인·결과 없는 요청을 따로 보이며 합치기만 있다",
  { tag: ["@story", "@US-SM-12"] },
  async ({ page }) => {
    test.setTimeout(300_000);
    const title = `US-SM-12 시도 원장 ${Date.now()}`;
    const first = requireData(
      (
        await createStrategyDocument({
          client: apiClient,
          body: {
            format: "yaml",
            source: mustReplace(GOLDEN, "퀄리티 모멘텀", title),
          },
        })
      ).data,
      "create strategy document",
    );

    // 첫 실행은 새 시도, 벤치마크만 바꾼 실행은 그 시도의 재확인이다. 벤치마크는 mock 어댑터의 종목
    // 어휘여야 한다 — 모르는 id 는 run 첫 단계에서 거절된다(#361).
    const counted = await completedRun(request(first));
    const benchmarkOnly = await completedRun(
      request(first, { benchmark_security_id: "sec-005930-1" }),
    );
    // 선택 종목 수를 바꾼 리비전은 새 시도, 그 뒤 제목만 바꾼 리비전은 같은 시도의 재확인이다.
    const changed = await revise(
      first,
      mustReplace(
        mustReplace(GOLDEN, "퀄리티 모멘텀", title),
        "selection_count: 20",
        "selection_count: 30",
      ),
    );
    const changedRun = await completedRun(request(changed));
    const retitled = await revise(
      changed,
      mustReplace(
        mustReplace(GOLDEN, "퀄리티 모멘텀", `${title} 제목만`),
        "selection_count: 20",
        "selection_count: 30",
      ),
    );
    const titleOnly = await completedRun(request(retitled));
    // 봉인 구간과 겹치는 요청은 거절되고 원장에 결과 없는 요청으로 남는다.
    const blocked = await startBacktest({
      client: apiClient,
      body: request(retitled, {
        environment: { ...REQUESTED_ENVIRONMENT, start: "2018-01-02" },
      }),
    });
    expect(blocked.response?.status).toBe(422);

    await page.goto("/research/strategies");
    const revisionToggle = page.getByRole("button", {
      name: `Revision 펼치기: ${title} 제목만 (${first.strategy_id})`,
      exact: true,
    });
    const pager = page.getByRole("navigation", { name: "전략 목록 페이지", exact: true });
    await expect(page.getByRole("table", { name: "저장 전략 목록" })).toBeVisible();
    // 전체 suite의 저장 전략은 20개를 넘는다. ID 정렬의 첫 페이지라는 가정을 하지 않는다.
    while ((await revisionToggle.count()) === 0) {
      const previousPage = await pager.innerText();
      const next = pager.getByRole("button", { name: "다음", exact: true });
      await expect(next).toBeEnabled(); // 마지막 페이지에도 없으면 저장/목록 회귀로 실패한다.
      await next.click();
      await expect(pager).not.toHaveText(previousPage);
      await expect(page.getByRole("table", { name: "저장 전략 목록" })).toBeVisible();
    }
    await revisionToggle.click();
    await page.getByRole("tab", { name: "시도 원장 2" }).click();
    const ledger = page.getByRole("tabpanel");
    await expect(ledger).toContainText("계열 시도 수 2회");

    await expect(runEntry(page, counted)).toContainText("시도로 셈");
    await expect(runEntry(page, benchmarkOnly)).toContainText("재확인");
    await expect(runEntry(page, changedRun)).toContainText("시도로 셈");
    await expect(runEntry(page, titleOnly)).toContainText("재확인");
    // 재확인은 자기 시도 행 아래에 묶인다.
    const rowOf = (runId: string) =>
      ledger.getByRole("row").filter({
        has: page.getByRole("link", { name: runId.slice(0, 12) }),
      });
    await expect(
      rowOf(counted).getByRole("link", { name: benchmarkOnly.slice(0, 12) }),
    ).toBeVisible();
    await expect(
      rowOf(changedRun).getByRole("link", { name: titleOnly.slice(0, 12) }),
    ).toBeVisible();
    await expect(
      ledger
        .getByRole("row")
        .filter({ hasText: "봉인 구간과 겹쳐 거절된 요청" }),
    ).toContainText("시도 수 제외");

    // 시도를 지우거나 계열을 나누는 버튼은 없고 합치기만 있다.
    await expect(ledger.getByRole("button")).toHaveText(["다른 계열과 합치기"]);
  },
);
