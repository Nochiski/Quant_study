/**
 * 정동민의 스토리 e2e — 홈 시장 블록(US-DM-17)과 DB 새로고침(US-DM-18).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다. 릴리스 게이트는 연습용(mock)
 * 원천으로 돈다 — 그 원천에서 지켜야 할 기준(시장 숫자를 지어내지 않는다, 받을 데이터가 없으니 새로고침 버튼이
 * 없다, 시장은 연구보다 위에 있다)을 본다. 실데이터 카드 값과 새로고침 흐름은 backend 통합 테스트가 본다.
 */
import { expect, test } from "@playwright/test";

test(
  "US-DM-17 연습용 데이터로 켜진 홈은 시장 숫자 없이 실제 데이터 연결 안내만 보이고 연구 카드가 그 아래에 있다",
  { tag: ["@story", "@US-DM-17"] },
  async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1, name: "홈" })).toBeVisible();
    const market = page.getByRole("region", { name: "시장" });
    await expect(market).toContainText("시장 정보는 실제 데이터를 연결해야 보여요");
    await expect(market).toContainText("연습용 데이터로 백테스트만 돌아요");
    // 연습용 숫자를 시장 정보처럼 보이지 않는다
    expect(await market.innerText()).not.toMatch(/\d/u);
    await expect(market.getByRole("heading", { name: "투자자별 순매수" })).toHaveCount(0);
    await expect(market.getByRole("heading", { name: "섹터맵" })).toHaveCount(0);
  },
);

test(
  "US-DM-18 받을 실제 데이터가 없는 연습용 데이터에서는 DB 새로고침 버튼이 보이지 않는다",
  { tag: ["@story", "@US-DM-18"] },
  async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("region", { name: "시장" })).toContainText(
      "시장 정보는 실제 데이터를 연결해야 보여요",
    );
    await expect(page.getByRole("button", { name: "DB 새로고침" })).toHaveCount(0);
    // 머리에 데이터 기준일도 그리지 않는다 — 기준이 될 원장이 없다
    await expect(page.getByText(/장 마감 ·/u)).toHaveCount(0);
  },
);
