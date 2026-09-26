/**
 * 한상목의 스토리 e2e — 키보드만으로 경로를 찾고 검증·저장·백테스트한다(US-SM-04).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/sm.md`다. 단축키가 어느 게이트를
 * 거치는지(검증·저장·백테스트가 버튼과 같은 게이트인지)는 `document-routes.test.tsx`가 route 수준에서
 * 잠근다. 여기서는 실제 backend 위에서 키보드만으로 끝까지 가는지를 본다.
 */
import { expect, test } from "@playwright/test";

import {
  expectPhase,
  GOLDEN,
  mustReplace,
  openEditor,
  replaceSource,
} from "../workbench-helpers";

test(
  "US-SM-04 키보드만으로 문서 경로를 찾고 검증·저장·백테스트까지 간다",
  { tag: ["@story", "@US-SM-04"] },
  async ({ page }) => {
    test.setTimeout(180_000);
    await openEditor(page, "/research/strategies/new");
    await replaceSource(
      page,
      mustReplace(GOLDEN, "퀄리티 모멘텀", "US-SM-04 키보드"),
    );
    await expectPhase(page, "검증 통과");

    // 명령 팔레트로 문서 경로를 찾는다. 고른 경로는 주소에 남고 계약 패널이 그 필드를 보인다.
    await page.keyboard.press("Control+K");
    const search = page.getByRole("combobox", {
      name: "명령과 문서 경로 검색",
    });
    await expect(search).toBeFocused();
    await search.fill("/risk/max_name_weight");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/[?&]path=%2Frisk%2Fmax_name_weight/u);
    await expect(
      page.getByRole("complementary", { name: "계약" }),
    ).toContainText("/risk/max_name_weight");

    // Ctrl+Enter: 버튼 "검증"과 같은 서버 검증이 다시 돈다.
    const compiled = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname ===
          "/api/v1/strategy-documents/compile" &&
        response.status() === 200,
    );
    await page.keyboard.press("Control+Enter");
    await compiled;
    await expectPhase(page, "검증 통과");

    // Ctrl+S: v1이 저장된다.
    await page.keyboard.press("Control+S");
    await expect(page).toHaveURL(
      /\/research\/strategies\/[^/]+\/revisions\/1(?:\?.*)?$/u,
    );
    await expectPhase(page, "저장됨");

    // Ctrl+Shift+Enter: 저장된 v1로 백테스트가 시작되고 끝난다.
    await page.keyboard.press("Control+Shift+Enter");
    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
      "completed",
      { timeout: 120_000 },
    );
    await expect(
      page.getByRole("article", { name: "백테스트 결과" }),
    ).toBeVisible({ timeout: 120_000 });
  },
);
