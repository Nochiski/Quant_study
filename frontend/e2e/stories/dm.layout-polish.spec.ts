import { expect, test, type Page } from "@playwright/test";

import { runSettingsInputs } from "../workbench-helpers";

const views = [
  { width: 1440, height: 900, theme: "light" as const },
  { width: 1440, height: 900, theme: "dark" as const },
  { width: 360, height: 800, theme: "light" as const },
];

const expectFocusedControlVisible = async (page: Page) => {
  expect(
    await page.evaluate(() => {
      const element = document.activeElement;
      if (
        !(element instanceof HTMLElement) ||
        (element.tabIndex < 0 && !element.isContentEditable)
      )
        return false;
      const bounds = element.getBoundingClientRect();
      const hit = document.elementFromPoint(
        bounds.x + bounds.width / 2,
        bounds.y + bounds.height / 2,
      );
      return (
        bounds.width > 0 &&
        bounds.height > 0 &&
        (hit === element || (hit !== null && element.contains(hit)))
      );
    }),
  ).toBe(true);
};

test(
  "US-DM-05 밝은·어두운 데스크톱과 좁은 화면에서 실행 설정을 읽고 키보드로 여닫는다",
  { tag: ["@story", "@US-DM-05"] },
  async ({ page }, testInfo) => {
    for (const view of views) {
      await test.step(`${view.width}px ${view.theme}`, async () => {
        await page.setViewportSize({ width: view.width, height: view.height });
        await page.emulateMedia({ colorScheme: view.theme });
        await page.goto("/research/strategies/new");
        await expect(page.locator("html")).toHaveAttribute(
          "data-theme",
          view.theme,
        );
        await expect(
          page.getByRole("tab", { name: "그래프", exact: true }),
        ).toBeVisible();
        await page.waitForLoadState("networkidle");
        await page.evaluate(() => document.fonts.ready);
        await page.screenshot({
          path: testInfo.outputPath(
            `${view.width}-${view.theme}-workbench.png`,
          ),
          fullPage: true,
        });

        const settings = runSettingsInputs(page);
        await settings.toggle.focus();
        await settings.toggle.press("Enter");
        await expect(settings.start).toBeVisible();
        await settings.start.focus();
        await expect(settings.start).toBeFocused();
        await page.screenshot({
          path: testInfo.outputPath(
            `${view.width}-${view.theme}-run-settings.png`,
          ),
          fullPage: true,
        });
        const evidence = await settings.start.evaluate((input) => {
          const bounds = input.getBoundingClientRect();
          const hit = document.elementFromPoint(
            bounds.x + bounds.width / 2,
            bounds.y + bounds.height / 2,
          );
          return {
            inputFontPx: Number.parseFloat(getComputedStyle(input).fontSize),
            visibleAtCenter:
              hit === input || (hit !== null && input.contains(hit)),
            left: bounds.left,
            right: bounds.right,
            viewportWidth: window.innerWidth,
            documentWidth: document.documentElement.scrollWidth,
          };
        });
        await testInfo.attach(`${view.width}-${view.theme}-layout-evidence`, {
          body: JSON.stringify(evidence, null, 2),
          contentType: "application/json",
        });
        const panel = page
          .getByRole("group", { name: "실행 환경", exact: true })
          .locator("..");
        const panelBounds = await panel.boundingBox();
        await panel.screenshot({
          path: testInfo.outputPath(
            `${view.width}-${view.theme}-run-panel.png`,
          ),
        });
        expect(panelBounds).not.toBeNull();
        expect(panelBounds!.x).toBeGreaterThanOrEqual(0);
        expect(panelBounds!.x + panelBounds!.width).toBeLessThanOrEqual(
          view.width,
        );
        expect(
          await panel.evaluate(
            (element) => element.scrollWidth <= element.clientWidth,
          ),
        ).toBe(true);
        expect(evidence.inputFontPx).toBeGreaterThanOrEqual(14);
        expect(evidence.visibleAtCenter).toBe(true);
        expect(evidence.left).toBeGreaterThanOrEqual(0);
        expect(evidence.right).toBeLessThanOrEqual(view.width);
        expect(evidence.documentWidth).toBeLessThanOrEqual(view.width);
        const startBounds = await settings.start.boundingBox();
        const market = page.getByRole("combobox", {
          name: "시장",
          exact: true,
        });
        const marketBounds = await market.boundingBox();
        expect(startBounds!.height).toBeGreaterThanOrEqual(40);
        expect(
          Math.abs(marketBounds!.height - startBounds!.height),
        ).toBeLessThanOrEqual(1);
        // native select의 초점과 실제 종속 칸 상태가 공통 화살표 스타일 뒤에도 유지된다.
        const impact = page.getByRole("combobox", {
          name: "가격 충격 모델",
          exact: true,
        });
        const coefficient = page.getByRole("spinbutton", {
          name: "가격 충격 계수",
          exact: true,
        });
        await impact.focus();
        await expect(impact).toBeFocused();
        await impact.selectOption("sqrt");
        await expect(coefficient).toBeEnabled();
        await impact.selectOption("fixed_bps");
        await expect(coefficient).toBeDisabled();
        await impact.press("Escape");
        await expect(settings.start).toBeHidden();
        await expect(settings.toggle).toBeFocused();
        await settings.toggle.press("Enter");
        await expect(settings.start).toBeVisible();
        await settings.toggle.press("Space");
        await expect(settings.start).toBeHidden();
        await expect(settings.toggle).toBeFocused();
        if (view.width === 360) {
          await settings.toggle.press("Enter");
          const closeSettings = page.getByRole("button", {
            name: "실행 설정 닫기",
            exact: true,
          });
          await closeSettings.focus();
          await expect(settings.start).toBeVisible();
          await settings.toggle.focus();
          await expect(settings.start).toBeVisible();
          const oosStart = page.getByLabel(/^OOS 시작일 \(선택\)/);
          await oosStart.focus();
          const settingsElement = await panel.elementHandle();
          expect(settingsElement).not.toBeNull();
          let leftSettings = false;
          // native 날짜 입력의 연·월·일 세그먼트를 지나 실제 다음 컨트롤로 이동한다.
          for (let index = 0; index < 8; index += 1) {
            await page.keyboard.press("Tab");
            leftSettings = await settingsElement!.evaluate(
              (element) => !element.contains(document.activeElement),
            );
            if (leftSettings) break;
          }
          expect(leftSettings).toBe(true);
          await expect(settings.start).toBeHidden();
          await expect(settings.toggle).not.toBeFocused();
          await expectFocusedControlVisible(page);
          const outline = page.getByRole("navigation", {
            name: "전략 구조",
            exact: true,
          });
          const contract = page.getByRole("complementary", {
            name: "계약",
            exact: true,
          });
          const openOutline = () =>
            page
              .getByRole("button", { name: "전략 구조", exact: true })
              .click();
          await openOutline();
          await expect(outline).toBeVisible();
          await page.getByRole("button", { name: "계약", exact: true }).click();
          await expect(contract).toBeVisible();
          await expect(outline).toBeHidden();
          await openOutline();
          await expect(outline).toBeVisible();
          await expect(contract).toBeHidden();
          const outlineBounds = await outline.locator("..").boundingBox();
          expect(outlineBounds!.height).toBeLessThanOrEqual(320);
          await page.screenshot({
            path: testInfo.outputPath("360-light-outline.png"),
            fullPage: true,
          });
          // 패널 경계에서 편집기로 나가는 양방향 키보드 경로도 실제로 접근 가능해야 한다.
          await page
            .getByRole("region", { name: "스니펫", exact: true })
            .getByRole("button")
            .last()
            .focus();
          await page.keyboard.press("Tab");
          await expectFocusedControlVisible(page);
          await page
            .getByRole("button", { name: "전략 구조 접기", exact: true })
            .focus();
          await page.keyboard.press("Shift+Tab");
          await expectFocusedControlVisible(page);
          // 실제 Tab 이동이 숨긴 계약 패널로 들어가거나 다른 패널에 가려지지 않는다.
          for (let index = 0; index < 6; index += 1) {
            await page.keyboard.press("Tab");
            await expectFocusedControlVisible(page);
          }
        }
        if (view.width === 1440 && view.theme === "light") {
          const outlineFilter = page.getByRole("searchbox", {
            name: "전략 구조 필터",
            exact: true,
          });
          await outlineFilter.focus();
          await expect(outlineFilter).toBeFocused();
          await page.setViewportSize({ width: 360, height: 800 });
          const resizedOutline = page.getByRole("navigation", {
            name: "전략 구조",
            exact: true,
          });
          await expect(resizedOutline).toBeVisible();
          await expect(outlineFilter).toBeFocused();
          expect(
            (await resizedOutline.locator("..").boundingBox())!.height,
          ).toBeLessThanOrEqual(320);
          await expectFocusedControlVisible(page);
          await page.setViewportSize({
            width: view.width,
            height: view.height,
          });
          for (const [name, route] of [
            ["strategy-list", "/research/strategies"],
            ["settings", "/settings"],
          ]) {
            await page.goto(route);
            await expect(page.getByRole("main")).toBeVisible();
            await page.waitForLoadState("networkidle");
            await page.evaluate(() => document.fonts.ready);
            await page.screenshot({
              path: testInfo.outputPath(`${name}.png`),
              fullPage: true,
            });
            expect(
              await page.evaluate(() => document.documentElement.scrollWidth),
            ).toBeLessThanOrEqual(view.width);
          }
        }
      });
    }
  },
);
