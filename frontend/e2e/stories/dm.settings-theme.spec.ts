/**
 * 정동민의 스토리 e2e — 화면 테마를 설정에서 고른다(US-DM-14, RD-11-A).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다.
 */
import { expect, test } from "@playwright/test";

test(
  "US-DM-14 설정에서 어둡게를 고르면 바로 바뀌고 새로고침 뒤에도 유지된다",
  { tag: ["@story", "@US-DM-14"] },
  async ({ page }) => {
    await page.goto("/settings");
    const themes = page.getByRole("radiogroup", { name: "화면 테마" });
    await expect(themes.getByRole("radio", { name: "시스템 설정 따르기 (기본)" })).toBeChecked();
    await themes.getByRole("radio", { name: "어둡게" }).check();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(
      page.getByRole("radiogroup", { name: "화면 테마" }).getByRole("radio", { name: "어둡게" }),
    ).toBeChecked();
    await page.getByRole("radiogroup", { name: "화면 테마" }).getByRole("radio", { name: "밝게" }).check();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  },
);
