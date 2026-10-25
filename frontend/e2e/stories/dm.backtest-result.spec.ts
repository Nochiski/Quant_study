/**
 * 정동민의 스토리 e2e — 백테스트 결과에서 핵심 숫자와 자산 곡선을 본다(US-DM-04).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다. 이 파일은 그 기준을
 * 사용자가 보는 화면(role·label·보이는 문구)과 서버 계좌 보고서 값으로 확인한다. 계좌 요약은
 * 360px 화면과 목차 키보드 이동도 확인하고, 기존 결과의 테마 계약은 workflow spec이 소유한다.
 */
import { expect, test } from "@playwright/test";
import type { AccountReport } from "../../src/shared/api/generated";

import {
  backtest,
  expectPhase,
  fillRunEnvironment,
  GOLDEN,
  mustReplace,
  openEditor,
  openRunSettingsGroup,
  openTraceWorkspace,
  openEditorWorkspace,
  replaceSource,
  runSettingsInputs,
  saveAndWaitForRevision,
} from "../workbench-helpers";

/**
 * "결과 한눈에"의 지표 카드 이름. 이름은 backend Metric Registry가 소유하고 화면은 그대로 옮긴다(SoT
 * 규칙). 이름이 바뀌면 스토리 수용 기준도 함께 고친다. 이 실행은 벤치마크 없이 돌아 Excess return은
 * 사용 불가 사유를 보이므로 값 검사에서 뺀다. 최종 평가액 카드는 계좌 보고서 값과 따로 대조한다.
 */
const SUMMARY_LABELS = [
  "Total return",
  "Sharpe ratio",
  "Maximum drawdown",
] as const;

/** 종료일 1년 안쪽의 OOS 시작일. 이 구간의 CAGR·칼마는 연율화하지 않는다(#274). */
const SHORT_OOS_START = "2026-03-02";

test(
  "US-DM-04 저장한 전략을 백테스트하면 결과 한눈에·두 축 카드·자산 곡선이 보인다",
  { tag: ["@story", "@US-DM-04"] },
  async ({ page }, testInfo) => {
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
    await openTraceWorkspace(page);
    const debuggerRegion = page.getByRole("region", { name: "중간 결과" });
    await debuggerRegion.getByText("입력 도움말", { exact: true }).click();
    await expect(debuggerRegion.locator(".strategy-debugger__scope-help")).toHaveAttribute("open", "");
    await expect(debuggerRegion).toContainText(
      "미리보기와 추적에는 실행 설정(기간·유니버스)이 필요해요.",
    );
    await expect(
      debuggerRegion.getByText("현재 실행 가능한 문서가 없습니다."),
    ).toHaveCount(0);
    await openEditorWorkspace(page);
    await fillRunEnvironment(page, { via: "band" });
    await expect(backtest(page)).toBeEnabled();

    // 서버가 거절할 초기 자본(0)은 패널이 먼저 막고, 요약 띠가 칸 이름과 이유를 말한다. "실행 설정
    // 고치기"가 그 칸으로 초점을 옮긴다(이슈 #260).
    const summary = runSettingsInputs(page).summary;
    const { toggle } = runSettingsInputs(page);
    const cash = page.getByRole("spinbutton", { name: "초기 자본 (KRW)" });
    await toggle.click();
    await openRunSettingsGroup(page, "고급 실행");
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
    const accountResponse = page.waitForResponse((response) =>
      /\/api\/v1\/backtests\/[^/]+\/account-report$/u.test(
        new URL(response.url()).pathname,
      ),
    );
    await backtest(page).click();

    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    const runId = new URL(page.url()).pathname.split("/").at(-1)!;
    await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
      "완료",
      { timeout: 120_000 },
    );
    const result = page.getByRole("article", { name: "백테스트 결과" });
    await expect(result).toBeVisible({ timeout: 120_000 });

    // 실제 mock backend가 계산한 계좌 보고서를 표시값과 대조한다. 손익 산식을 화면 테스트에 복제하지 않는다.
    const accountHttp = await accountResponse;
    expect(accountHttp.ok()).toBe(true);
    const account = (await accountHttp.json()) as AccountReport;
    expect(account.status).toBe("available");
    expect(account.summary).not.toBeNull();
    const accountSummary = account.summary!;
    const accountPanel = result.getByRole("region", {
      name: "초기자본 기준 계좌 요약",
      exact: true,
    });
    await expect(accountPanel).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath("backtest-result-desktop.png"),
      fullPage: true,
    });
    await expect(accountPanel).toContainText(account.interpretation!);
    await expect(accountPanel).toContainText(
      "전체 구간(FULL)의 총수익률은 첫 계좌 기록을 기준",
    );
    const amount = (value: number) =>
      `₩${value.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}`;
    for (const [label, value] of [
      ["초기자본 (시작조건)", amount(accountSummary.initial_cash)],
      ["최종 평가액", amount(accountSummary.final_equity)],
      ["마지막 기록의 현금", amount(accountSummary.final_cash)],
      ["비용 반영 후 손익", amount(accountSummary.pnl)],
      [
        "초기자본 대비 수익률",
        new Intl.NumberFormat("ko-KR", {
          style: "percent",
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        }).format(accountSummary.total_return),
      ],
      ["첫 기록일", accountSummary.first_snapshot_date],
      ["마지막 기록일", accountSummary.last_snapshot_date],
    ]) {
      await expect(
        accountPanel.getByText(label, { exact: true }).locator("xpath=.."),
      ).toContainText(value);
    }
    await expect(
      accountPanel
        .getByText("초기자본 (시작조건)", { exact: true })
        .locator("xpath=.."),
    ).not.toContainText(accountSummary.first_snapshot_date);
    const accountLink = result
      .getByRole("navigation", { name: "결과 둘러보기" })
      .getByRole("link", { name: "초기자본 기준 계좌 요약", exact: true });
    await accountLink.focus();
    await accountLink.press("Enter");
    await expect(accountPanel).toBeFocused();
    const accountViewport = page.viewportSize();
    await page.setViewportSize({ width: 360, height: 800 });
    await accountPanel
      .getByText("계좌 요약 재현성 정보", { exact: true })
      .click();
    await expect(
      accountPanel.getByText(account.provenance.run_fingerprint, {
        exact: true,
      }),
    ).toBeVisible();
    expect(
      await accountPanel.evaluate(
        (element) => element.scrollWidth <= element.clientWidth,
      ),
    ).toBe(true);
    // 폭 회귀가 나면 브라우저의 실제 배치값을 실패 메시지에 남겨 탈출한 요소와 caption 기준 부모를 찾는다.
    const widthDiagnostics = await page.evaluate(() => {
      const describe = (element: Element) => {
        const rect = element.getBoundingClientRect();
        return {
          tag: element.tagName,
          className: element.getAttribute("class"),
          text: element.textContent?.trim().slice(0, 100),
          left: rect.left,
          right: rect.right,
          width: rect.width,
          clientWidth: element.clientWidth,
          scrollWidth: element.scrollWidth,
        };
      };
      const clippedByAncestor = (element: Element) => {
        let ancestor = element.parentElement;
        while (ancestor !== null) {
          const overflow = getComputedStyle(ancestor).overflowX;
          if (["auto", "scroll", "hidden", "clip"].includes(overflow))
            return true;
          ancestor = ancestor.parentElement;
        }
        return false;
      };
      return {
        viewport: window.innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        documentClientWidth: document.documentElement.clientWidth,
        bodyWidth: document.body.scrollWidth,
        bodyClientWidth: document.body.clientWidth,
        unclippedOverflow: [...document.body.querySelectorAll("*")]
          .filter(
            (element) =>
              element.getBoundingClientRect().right > window.innerWidth &&
              !clippedByAncestor(element),
          )
          .map(describe),
        captions: [
          ...document.querySelectorAll<HTMLTableCaptionElement>(
            "caption.sr-only",
          ),
        ].map((caption) => ({
          ...describe(caption),
          offsetParent: caption.offsetParent
            ? describe(caption.offsetParent)
            : null,
          clip: getComputedStyle(caption).clip,
        })),
      };
    });
    expect(
      widthDiagnostics.documentWidth <= widthDiagnostics.viewport,
      JSON.stringify(widthDiagnostics, null, 2),
    ).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath("backtest-result-mobile.png"),
      fullPage: true,
    });
    if (accountViewport) await page.setViewportSize(accountViewport);

    // 새 결과는 원본 주문 이벤트를 기록한다. 옛 형식의 미기록 안내로 대체되면 안 된다(#411).
    const orderEvents = result.getByRole("region", { name: "주문 상태 기록" });
    await expect(orderEvents).toBeVisible();
    await expect(orderEvents).toContainText(/주문 상태 이벤트.*(?:건|0건)/u);
    await expect(orderEvents).not.toContainText("이전 형식 결과");
    const records = orderEvents.getByText("주문 상태 원본 보기", {
      exact: true,
    });
    if (await records.count()) {
      await records.click();
      await expect(orderEvents.getByRole("table")).toBeVisible();
    }

    // 머리말에서 다음 행동으로 바로 간다(RD-07-E). 저장 리비전으로 돈 실행이라 "규칙 고치기"가 있다.
    for (const name of ["동일 설정 재실행", "AI에게 결과 묻기"])
      await expect(page.getByRole("button", { name })).toBeVisible();
    await expect(page.getByRole("link", { name: "규칙 고치기" })).toHaveAttribute(
      "href",
      /\/research\/strategies\/[^/]+\/revisions\/1$/u,
    );

    const highlights = result.getByRole("region", { name: "결과 한눈에" });
    // 지표마다 이름 바로 뒤에 서버가 계산한 값이 숫자로 붙는다. 수익 방향이 있는 값은 낭독 문구와 부호가
    // 먼저 온다. 값이 없으면 "계산 불가"라 여기서 걸린다.
    for (const label of SUMMARY_LABELS)
      await expect(
        highlights.getByText(label, { exact: true }).locator("xpath=.."),
      ).toHaveText(new RegExp(`^${label}\\s*(?:상승\\s*\\+|하락\\s*−|-)?\\d`, "u"));
    await expect(
      highlights.getByText("Excess return", { exact: true }).locator("xpath=.."),
    ).toContainText("벤치마크 값이 비어 계산할 수 없습니다");
    await expect(
      highlights.getByText("최종 평가액", { exact: true }).locator("xpath=.."),
    ).toContainText(
      `₩${accountSummary.final_equity.toLocaleString("ko-KR", { maximumFractionDigits: 0 })}`,
    );
    // 두 축 카드: 손익비는 원문과 정의를 함께, 손익 구조는 실현 통계라는 한계를 함께 보인다(RD-07-C).
    await expect(
      result.getByRole("region", { name: "종목 선정이 효과가 있었나" }),
    ).toBeVisible();
    const pnl = result.getByRole("region", { name: "손익 구조는 어땠나" });
    await expect(pnl).toContainText("profit factor · 이익 합 ÷ 손실 합");
    await expect(pnl).toContainText(
      "실제로 청산된 거래로 계산한 실현 통계예요. 지금 전략에는 손절·익절 규칙이 없어서 목표 손익비는 표시하지 않아요.",
    );
    // 자산 곡선과 낙폭은 같은 가로축으로 붙어 있고 그래프마다 한 문장 요약이 있다(RD-07-D).
    const curves = result.getByRole("region", { name: "자산 곡선과 낙폭" });
    await expect(curves.getByRole("img", { name: /^자산 곡선\. /u })).toBeVisible();
    await expect(curves.getByRole("img", { name: /^낙폭\. /u })).toBeVisible();
    await expect(curves.getByText("전략", { exact: true })).toBeVisible();
    // 목차의 키보드 선택은 원본 표로 초점을 옮긴다. 긴 결과를 탭으로 끝까지 훑지 않아도 된다.
    const rawMetricsLink = result
      .getByRole("navigation", { name: "결과 둘러보기" })
      .getByRole("link", { name: "Raw metric table", exact: true });
    await rawMetricsLink.focus();
    await rawMetricsLink.press("Enter");
    const rawMetrics = result.getByRole("region", {
      name: "Raw metric table",
      exact: true,
    });
    await expect(rawMetrics).toBeFocused();
    await expect(
      rawMetrics.getByRole("table", { name: "Raw metric table", exact: true }),
    ).toBeVisible();
    const originalViewport = page.viewportSize();
    await page.setViewportSize({ width: 360, height: 800 });
    await rawMetrics.focus();
    await rawMetrics.press("ArrowRight");
    await expect
      .poll(() => rawMetrics.evaluate((element) => element.scrollLeft))
      .toBeGreaterThan(0);
    if (originalViewport) await page.setViewportSize(originalViewport);
    // 1년 미만 OOS 구간: CAGR·칼마는 값 대신 사유를 보이고, 총수익률은 값으로 보인다.
    const oosRow = (metricId: string) =>
      result
        .getByRole("row")
        .filter({ hasText: `OOS ${SHORT_OOS_START}` })
        .filter({ has: page.getByText(metricId, { exact: true }) });
    for (const metricId of ["cagr", "calmar"]) {
      await expect(oosRow(metricId)).toContainText("계산 불가");
      await expect(oosRow(metricId)).toContainText(
        "기간이 1년보다 짧아 연율로 바꾸지 않습니다",
      );
    }
    await expect(oosRow("total_return")).toContainText(/\d\.\d{2}%/u);

    // 계좌 보고서만 실패해도 원본 결과는 유지되고, 일시적 오류는 실제 서버 값을 다시 읽어 복구한다.
    const accountRoute = `**/api/v1/backtests/${runId}/account-report`;
    await page.route(accountRoute, (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "계좌 보고서 일시적 조회 실패" }),
      }),
    );
    await page.reload();
    await expect(accountPanel).toContainText(
      "계좌 요약을 불러오지 못했습니다.",
    );
    await expect(
      rawMetrics.getByRole("table", { name: "Raw metric table", exact: true }),
    ).toBeVisible();
    await expect(
      result.getByRole("img", { name: /^자산 곡선\. /u }),
    ).toBeVisible();
    const retryAccount = accountPanel.getByRole("button", {
      name: "계좌 요약 다시 불러오기",
    });
    await expect(retryAccount).toBeVisible();
    await page.unroute(accountRoute);
    await retryAccount.click();
    await expect(accountPanel).toContainText("계좌 요약 계산 완료");
    await expect(accountPanel).not.toContainText(
      "계좌 요약을 불러오지 못했습니다.",
    );
    await expect(
      accountPanel
        .getByText("최종 평가액", { exact: true })
        .locator("xpath=.."),
    ).toContainText(amount(accountSummary.final_equity));

    // 결과 파일을 다시 읽을 수 없는 영구 오류는 원본 결과와 별도로 안내하고 재시도를 제공하지 않는다.
    await page.route(accountRoute, (route) =>
      route.fulfill({
        status: 410,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "backtest.result.unreadable",
            message: "계좌 보고서 결과 파일을 읽을 수 없습니다.",
          },
        }),
      }),
    );
    await page.reload();
    await expect(accountPanel).toContainText(
      "계좌 요약을 불러오지 못했습니다.",
    );
    await expect(accountPanel.getByRole("alert")).toContainText(
      "이 실행의 결과 파일을 읽을 수 없습니다.",
    );
    await expect(
      rawMetrics.getByRole("table", { name: "Raw metric table", exact: true }),
    ).toBeVisible();
    await expect(retryAccount).toHaveCount(0);
    await page.unroute(accountRoute);

    // 나중에 다시 찾을 수 있다: 백테스트 이력에 방금 실행이 완료 상태로 남는다.
    await page.getByRole("link", { name: "백테스트" }).click();
    await expect(
      page.getByRole("heading", { name: "백테스트 이력" }),
    ).toBeVisible();
    const historyRow = page.getByRole("row").filter({ hasText: runId });
    await expect(historyRow).toContainText("완료");
    // 결과 칸은 결과 파일을 열지 않고 서버가 실은 머리 지표를 보인다(RD-06-A).
    for (const label of ["총수익률", "샤프", "최대 낙폭"])
      await expect(historyRow).toContainText(label);
  },
);
