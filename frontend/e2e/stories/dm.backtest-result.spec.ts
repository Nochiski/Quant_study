/**
 * 정동민의 스토리 e2e — 백테스트 결과에서 핵심 숫자와 자산 곡선을 본다(US-DM-04).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다. 이 파일은 그 기준을
 * 사용자가 보는 화면(role·label·보이는 문구)으로만 확인한다. 결과 표현의 반응형·테마 계약은
 * `workbench.workflow.spec.ts`가 소유하므로 여기서 다시 재지 않는다.
 */
import { expect, test } from "@playwright/test";

import {
  backtest,
  expectPhase,
  GOLDEN,
  mustReplace,
  openEditor,
  replaceSource,
  saveAndWaitForRevision,
} from "../workbench-helpers";

/**
 * 결과 상단에 고정으로 보이는 여섯 지표의 이름. 이름은 backend Metric Registry가 소유하고 화면은
 * 그대로 옮긴다(SoT 규칙). 이름이 바뀌면 스토리 수용 기준도 함께 고친다.
 */
const HIGHLIGHT_LABELS = [
  "Total return",
  "Sharpe ratio",
  "Maximum drawdown",
  "Calmar ratio",
  "Turnover",
  "Closed trades",
] as const;

test(
  "US-DM-04 저장한 전략을 백테스트하면 핵심 성과 지표 여섯 개와 자산 곡선이 보인다",
  { tag: ["@story", "@US-DM-04"] },
  async ({ page }) => {
    test.setTimeout(180_000);
    await openEditor(page, "/research/strategies/new");
    await replaceSource(
      page,
      mustReplace(GOLDEN, "퀄리티 모멘텀", "US-DM-04 결과 읽기"),
    );
    await expectPhase(page, "검증 통과");
    await saveAndWaitForRevision(page, 1);

    await expect(backtest(page)).toBeEnabled();
    await backtest(page).click();

    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    const runId = new URL(page.url()).pathname.split("/").at(-1)!;
    await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
      "completed",
      { timeout: 120_000 },
    );
    const result = page.getByRole("article", { name: "백테스트 결과" });
    await expect(result).toBeVisible({ timeout: 120_000 });

    const highlights = result.getByRole("region", { name: "핵심 성과 지표" });
    // 지표마다 이름 바로 뒤에 서버가 계산한 값이 숫자로 붙는다. 값이 없으면 "N/A"라 여기서 걸린다.
    for (const label of HIGHLIGHT_LABELS)
      await expect(
        highlights.getByText(label, { exact: true }).locator("xpath=.."),
      ).toHaveText(new RegExp(`^${label}\\s*[-₩]?\\d`, "u"));
    await expect(
      result.getByRole("img", { name: "Equity curve 차트" }),
    ).toBeVisible();

    // 나중에 다시 찾을 수 있다: 백테스트 이력에 방금 실행이 완료 상태로 남는다.
    await page.getByRole("link", { name: "백테스트" }).click();
    await expect(
      page.getByRole("heading", { name: "백테스트 이력" }),
    ).toBeVisible();
    await expect(
      page.getByRole("row").filter({ hasText: runId }),
    ).toContainText("completed");
  },
);
