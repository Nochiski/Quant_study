/**
 * 한상목의 스토리 e2e — 필드의 단위·범위·기본값을 계약 패널에서 확인한다(US-SM-02).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/sm.md`다. 표시 값(`5%`)과
 * 설명 문장은 backend runtime schema·contract와 frontend i18n이 소유하며, 여기서는 사용자가 전략
 * 구조에서 필드를 골랐을 때 그 사실이 계약 패널에 보이는지만 본다.
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
  "US-SM-02 전략 구조에서 필드를 고르면 계약 패널이 단위·범위·표시 값을 알려 준다",
  { tag: ["@story", "@US-SM-02"] },
  async ({ page }) => {
    await openEditor(page, "/research/strategies/new");
    await replaceSource(
      page,
      mustReplace(GOLDEN, "퀄리티 모멘텀", "US-SM-02 계약 확인"),
    );
    await expectPhase(page, "검증 통과");

    // 필드 이름을 외우지 않아도 된다: 전략 구조를 걸러 찾고 고른다.
    await page.getByLabel("전략 구조 필터").fill("max_name_weight");
    const outline = page.getByRole("tree", { name: "StrategySpec 문서 구조" });
    await outline.getByRole("treeitem", { name: /max_name_weight/u }).click();

    const contract = page.getByRole("complementary", { name: "계약" });
    // 제목은 필드의 사람 말 이름이고 JSON Pointer는 `Path` 항목으로 내려갔다(lang2 P1-03 화면 어휘).
    await expect(
      contract.getByRole("heading", { name: "종목별 최대 목표 비중 한도" }),
    ).toBeVisible();
    await expect(
      contract
        .getByRole("term")
        .filter({ hasText: /^Path$/u })
        .locator("xpath=following-sibling::*[1]"),
    ).toHaveText("/risk/max_name_weight");
    await expect(contract).toContainText("종목별 최대 목표 비중 한도");
    // 항목 이름(term) 바로 다음 칸(definition)이 그 값이다. 저장 값 0.05는 화면에서 5%로 읽힌다.
    const valueOf = (term: string) =>
      contract
        .getByRole("term")
        .filter({ hasText: term })
        .locator("xpath=following-sibling::*[1]");
    await expect(valueOf("저장 값")).toHaveText("0.05");
    await expect(valueOf("표시 값")).toHaveText("5%");
    await expect(valueOf("기본값")).toHaveText("0.1");
    await expect(valueOf("범위")).toHaveText("> 0 · ≤ 1");
    await expect(valueOf("단위")).toHaveText("ratio → %");
  },
);
