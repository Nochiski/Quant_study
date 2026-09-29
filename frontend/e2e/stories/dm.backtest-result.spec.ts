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
  fillRunEnvironment,
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

/** 종료일 1년 안쪽의 OOS 시작일. 이 구간의 CAGR·칼마는 연율화하지 않는다(#274). */
const SHORT_OOS_START = "2026-03-02";

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

    // 실행 설정(기간·유니버스)은 전략 문서 밖에 있고 사용자가 정해야 실행이 열린다(P3-02). 비어 있으면
    // 백테스트가 막히고, 요약 띠가 빈 칸 이름과 "실행 설정 채우기"를 보인다.
    await expect(backtest(page)).toBeDisabled();
    // 같은 상태에서 중간 결과도 막힌 이유를 실행 설정 한 문장으로만 말한다. 문서는 멀쩡한데 "실행 가능한
    // 문서가 없다"가 함께 뜨면 사용자가 문서를 고치러 간다(이슈 #260).
    const debuggerRegion = page.getByRole("region", { name: "중간 결과" });
    await expect(debuggerRegion).toContainText(
      "추적은 실행 설정 위에서 돕니다.",
    );
    await expect(
      debuggerRegion.getByText("현재 실행 가능한 문서가 없습니다."),
    ).toHaveCount(0);
    await fillRunEnvironment(page, { via: "band" });
    await expect(backtest(page)).toBeEnabled();

    // 서버가 거절할 초기 자본(0)은 패널이 먼저 막고, 요약 띠가 칸 이름과 이유를 말한다. "실행 설정
    // 고치기"가 그 칸으로 초점을 옮긴다(이슈 #260).
    const summary = page.getByRole("region", { name: "실행 설정 요약" });
    const toggle = page.getByLabel("실행 설정 열기");
    const cash = page.getByRole("spinbutton", { name: "초기 자본 (KRW)" });
    await toggle.click();
    await cash.fill("0");
    await toggle.click();
    await expect(summary).toContainText(
      "실행 설정의 초기 자본 칸을 고치세요: 0보다 큰 숫자를 입력하세요.",
    );
    await expect(backtest(page)).toBeDisabled();
    await summary.getByRole("button", { name: "실행 설정 고치기" }).click();
    await expect(cash).toBeFocused();
    await cash.fill("100000000");
    // OOS 를 종료일(2026-08-31) 1년 안쪽에서 시작해, 그 구간의 CAGR 이 연율화되지 않는 것을 본다(#274).
    await page.getByLabel(/^OOS 시작일/u).fill(SHORT_OOS_START);
    await toggle.click();
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
    // 1년 미만 OOS 구간: CAGR·칼마는 값 대신 사유를 보이고, 총수익률은 값으로 보인다.
    const oosRow = (metricId: string) =>
      result
        .getByRole("row")
        .filter({ hasText: `OOS ${SHORT_OOS_START}` })
        .filter({ has: page.getByText(metricId, { exact: true }) });
    for (const metricId of ["cagr", "calmar"]) {
      await expect(oosRow(metricId)).toContainText("N/A");
      await expect(oosRow(metricId)).toContainText(
        "기간이 1년보다 짧아 연율로 바꾸지 않습니다",
      );
    }
    await expect(oosRow("total_return")).toContainText(/\d\.\d{2}%/u);

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
