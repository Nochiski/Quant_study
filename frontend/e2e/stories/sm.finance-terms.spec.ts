/**
 * 한상목의 스토리 e2e — 모르는 금융 개념을 편집 흐름 안에서 설명받는다(US-SM-09).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/sm.md`다. 두 경로를 본다. 하나는
 * AI 어시스턴트에게 개념을 묻는 것이고(대본 공급자의 "샤프" 낱말이 `concept_answer`를 고른다), 다른
 * 하나는 전략 구조에서 필드를 골랐을 때 계약 패널이 보이는 한글 설명이다. 설명 문장의 owner는 대본과
 * frontend i18n이며 여기서는 화면에 닿는지만 본다.
 */
import { expect, test } from "@playwright/test";

import {
  ask,
  ensureProvider,
  openAssistant,
  progress,
  transcript,
} from "../assistant-helpers";
import {
  expectPhase,
  GOLDEN,
  mustReplace,
  openEditor,
  replaceSource,
} from "../workbench-helpers";

test(
  "US-SM-09 편집 중에 AI와 계약 패널로 금융 개념의 뜻을 확인한다",
  { tag: ["@story", "@US-SM-09"] },
  async ({ page }) => {
    test.setTimeout(120_000);
    await ensureProvider(page);
    await openEditor(page, "/research/strategies/new");
    await replaceSource(
      page,
      mustReplace(GOLDEN, "퀄리티 모멘텀", "US-SM-09 용어 배우기"),
    );
    await expectPhase(page, "검증 통과");

    // AI에게 결과 화면에서 본 용어를 묻는다. 편집기를 떠나지 않는다.
    await openAssistant(page);
    await ask(page, "샤프 비율이 무슨 뜻이야?");
    await expect(transcript(page)).toContainText(
      "위험 한 단위당 얼마나 벌었는지를 나타내는 숫자입니다.",
    );
    await expect(progress(page)).toHaveText("답변이 완료되었습니다.");
    await expect(page).toHaveURL(/\/research\/strategies\/new/u);

    // 문서의 금융 필드는 전략 구조에서 고르면 계약 패널이 한글 뜻을 보인다.
    await page.getByLabel("전략 구조 필터").fill("selection_count");
    await page
      .getByRole("tree", { name: "StrategySpec 문서 구조" })
      .getByRole("treeitem", { name: /^selection_count/u })
      .click();
    const contract = page.getByRole("complementary", { name: "계약" });
    await expect(
      contract.getByRole("heading", { name: "/portfolio/selection_count" }),
    ).toBeVisible();
    await expect(contract).toContainText("롱 포트폴리오에 선택할 종목 수");
  },
);
