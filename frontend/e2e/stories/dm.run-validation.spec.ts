/**
 * US-DM-10: 실제 mock 실행의 서버 통계를 읽고 근거·조회 실패를 구분한다.
 * AI 검증 요약은 후속 US-DM-12가 소유하며 이 검사로 완료 처리하지 않는다.
 */
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { RunValidationReport } from "../../src/shared/api/generated";
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

const runValidationStory = async (page: Page, testInfo: TestInfo, viewport: { width: number; height: number }) => {
      test.setTimeout(180_000);
      await page.setViewportSize(viewport);
      await page.emulateMedia({ colorScheme: viewport.width === 360 ? "dark" : "light" });
      await openEditor(page, "/research/strategies/new");
      const source = mustReplace(GOLDEN, "퀄리티 모멘텀", `US-DM-10 ${viewport.width}px 검증 카드`);
      await replaceSource(page, source);
      await expectPhase(page, "검증 통과");
      await saveAndWaitForRevision(page, 1);
      const revisionUrl = page.url();
      await fillRunEnvironment(page);
      const response = page.waitForResponse(value => /\/api\/v1\/backtests\/[^/]+\/validation-report$/u.test(new URL(value.url()).pathname));
      await backtest(page).click();
      await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
      const http = await response;
      expect(http.ok()).toBe(true);
      const report = await http.json() as RunValidationReport;
      expect(report.dsr.trial_count).toBe(1);
      const card = page.getByRole("region", { name: "운일까, 튼튼할까", exact: true });
      await expect(card).toBeVisible();
      await expect(card).toContainText("미래 수익을 보장하거나 전략의 통과 여부를 판정하지 않습니다.");
      await expect(card).toContainText("후보 선택 당시 기록된 통계 스냅숏과 다르며");
      await expect(card.locator("time")).toHaveAttribute("datetime", report.observed_at);
      const psr = card.getByRole("region", { name: "샤프 기준 초과 통계(PSR)" });
      const dsr = card.getByRole("region", { name: "시도 횟수 보정 통계(DSR)" });
      await expect(dsr).toContainText(`계열 시도 수 N: ${report.dsr.trial_count ?? "값 없음"}`);
      await expect(dsr).toContainText(`대표 샤프 자료 수: ${report.dsr.representative_count}`);
      if (report.psr === null) await expect(psr).toContainText("이 실행에 PSR 기록이 없습니다.");
      else await expect(psr).toContainText(`PSR 표본 수: ${report.psr.sample_count}`);
      await testInfo.attach("검증 카드 기본 읽기", { body: await card.screenshot(), contentType: "image/png" });

      const basis = card.locator("summary").filter({ hasText: "계산 근거" });
      const rawPsr = card.getByText("PSR 원래 수치", { exact: true }).locator("..");
      const rawDsr = card.getByText("DSR 원래 수치", { exact: true }).locator("..");
      await expect(rawDsr).not.toBeVisible();
      await basis.focus();
      await expect(basis).toBeFocused();
      await page.keyboard.press("Enter");
      await expect(rawDsr).toBeVisible();
      await expect(rawPsr).toContainText(String(report.psr?.value ?? "값 없음"));
      await expect(rawDsr).toContainText(String(report.dsr.value ?? "값 없음"));
      await expect(card.getByText(report.run_fingerprint, { exact: true })).toBeVisible();
      await expect(card).toContainText("처음 완료된 실행에서 정합니다.");
      await expect(card).toContainText("기록되지 않은 시도를 모두 보정했다고 볼 수 없습니다.");
      const navigation = page.getByRole("navigation", { name: "결과 둘러보기" });
      await navigation.getByRole("link", { name: "Raw metric table", exact: true }).click();
      await expect(page.getByRole("region", { name: "Raw metric table", exact: true }).getByRole("table")).toBeVisible();
      await navigation.getByRole("link", { name: "초기자본 기준 계좌 요약", exact: true }).click();
      await expect(page.getByRole("region", { name: "초기자본 기준 계좌 요약", exact: true })).toContainText("초기자본 (시작조건)");
      await navigation.getByRole("link", { name: "Manifest · 데이터 경고 · 재현성 정보", exact: true }).click();
      const manifestFingerprint = page.getByRole("group", { name: "Manifest · 데이터 경고 · 재현성 정보", exact: true }).getByTitle(report.run_fingerprint, { exact: true });
      await expect(manifestFingerprint).toBeVisible();
      await expect(manifestFingerprint).toHaveText(`${report.run_fingerprint.slice(0, 16)}…`);
      await navigation.getByRole("link", { name: "운일까, 튼튼할까", exact: true }).click();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await testInfo.attach("검증 카드 계산 근거", { body: await card.screenshot(), contentType: "image/png" });
      await basis.focus();
      await page.keyboard.press("Space");
      await expect(rawDsr).not.toBeVisible();

      // 같은 저장 전략의 다른 선택 설정을 실행해도 옛 결과 파일을 고쳐 쓰지 않는다.
      const resultUrl = http.url().replace(/validation-report$/u, "result");
      const immutableResult = await (await page.request.get(resultUrl)).text();
      const originalRunUrl = page.url();
      await openEditor(page, revisionUrl);
      await replaceSource(page, mustReplace(source, "selection_count: 20", "selection_count: 19"));
      await expectPhase(page, "검증 통과");
      await saveAndWaitForRevision(page, 2);
      await fillRunEnvironment(page);
      const secondReportResponse = page.waitForResponse(value => /\/validation-report$/u.test(new URL(value.url()).pathname) && value.url() !== http.url());
      await backtest(page).click();
      const secondReport = await secondReportResponse;
      expect(secondReport.ok()).toBe(true);
      expect((await secondReport.json() as RunValidationReport).dsr.trial_count).toBe(2);
      await page.goto(originalRunUrl);
      await expect(card.getByRole("button", { name: "원장 기준 다시 조회" })).toBeEnabled();
      const refreshedResponse = page.waitForResponse(value => value.url() === http.url());
      await card.getByRole("button", { name: "원장 기준 다시 조회" }).click();
      const refreshed = await (await refreshedResponse).json() as RunValidationReport;
      expect(refreshed.dsr.trial_count).toBe(2);
      await expect(dsr).toContainText("계열 시도 수 N: 2");
      expect(await (await page.request.get(resultUrl)).text()).toBe(immutableResult);

      // 조회 실패만 주입한다. 실제 통계 원본은 위 mock backend 응답을 그대로 대조했다.
      const endpoint = http.url();
      await page.route(endpoint, route => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: { message: "일시적인 조회 장애" } }) }));
      await card.getByRole("button", { name: "원장 기준 다시 조회" }).click();
      await expect(card.getByRole("alert")).toBeVisible();
      await expect(card).toContainText("아래 수치는 마지막으로 확인한 값입니다.");
      await expect(card.locator("time")).toHaveAttribute("datetime", refreshed.observed_at);
      await expect(dsr).toBeVisible();
      await page.unroute(endpoint);
      const recovered = page.waitForResponse(value => value.url() === endpoint && value.status() === 200);
      await card.getByRole("button", { name: "원장 기준 다시 조회" }).click();
      await recovered;
      await expect(card.getByRole("alert")).toHaveCount(0);
      await expect(card.getByText(/아래 수치는 마지막으로 확인한 값입니다/u)).toHaveCount(0);

      let lostRequests = 0;
      await page.route(endpoint, route => {
        lostRequests++;
        return route.fulfill({ status: 410, contentType: "application/json", body: JSON.stringify({ detail: { code: "backtest.result.unreadable", message: "결과 파일을 읽을 수 없습니다" } }) });
      });
      await card.getByRole("button", { name: "원장 기준 다시 조회" }).click();
      await expect(card.getByRole("alert")).toContainText("이 실행의 결과 파일을 읽을 수 없습니다.");
      await expect(card.getByRole("button", { name: "원장 기준 다시 조회" })).toHaveCount(0);
      await expect(dsr).toHaveCount(0);
      // 기본 실행 상태 query의 실제 재접속 조회를 양성 대조로 사용한다.
      const statusUrl = endpoint.replace(/\/validation-report$/u, "");
      const reconnectedStatus = page.waitForResponse(value => value.url() === statusUrl && value.ok());
      await page.context().setOffline(true);
      await page.context().setOffline(false);
      await reconnectedStatus;
      await expect(card.getByRole("alert")).toBeVisible();
      expect(lostRequests).toBe(1);
};

test(
  "US-DM-10 1440px에서 원래 통계·키보드 근거·조회 오류를 구별한다",
  { tag: ["@story", "@US-DM-10"] },
  async ({ page }, testInfo) => runValidationStory(page, testInfo, { width: 1440, height: 900 }),
);

test(
  "US-DM-10 360px에서 원래 통계·키보드 근거·조회 오류를 구별한다",
  { tag: ["@story", "@US-DM-10"] },
  async ({ page }, testInfo) => runValidationStory(page, testInfo, { width: 360, height: 800 }),
);
