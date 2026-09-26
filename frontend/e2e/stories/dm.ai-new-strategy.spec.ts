/**
 * 정동민의 스토리 e2e — 빈 새 전략에서 말로 한 아이디어를 AI가 전략으로 바꿔 주고 바로 백테스트한다
 * (US-DM-03).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다. 공급자는 backend 대본
 * adapter이고, 질문의 "새 전략" 낱말이 `idea_to_new_strategy` 대본을 고른다(`_scenarios.py`). 그 대본은
 * 편집기의 `schema_version` 줄을 살려 전략 전체를 제안한다. 제안 미리보기·덮어쓰기 확인은 같은 스토리의
 * `assistant.workflow.spec.ts` 테스트가 본다.
 */
import { expect, test } from "@playwright/test";

import {
  ask,
  assistant,
  ensureProvider,
  openAssistant,
} from "../assistant-helpers";
import { backtest, expectPhase, openEditor } from "../workbench-helpers";

/** 대본이 제안하는 전략 제목(`_scenarios.py`의 `_IDEA_TITLE`). */
const IDEA_TITLE = "KRX 대형 모멘텀";

test(
  "US-DM-03 빈 새 전략에서 AI에게 아이디어를 말해 받은 전략을 적용하고 백테스트한다",
  { tag: ["@story", "@US-DM-03"] },
  async ({ page }) => {
    test.setTimeout(240_000);
    await ensureProvider(page);
    await openEditor(page, "/research/strategies/new");
    // 빈 새 전략은 아직 전략이 아니다: 실행할 수 없다.
    await expect(backtest(page)).toBeDisabled();

    await openAssistant(page);
    await ask(page, "최근 많이 오른 대형주를 사는 새 전략을 만들어 줘");

    const card = assistant(page).getByRole("article", { name: IDEA_TITLE });
    await expect(card).toBeVisible({ timeout: 60_000 });
    await expect(card).toContainText("검증 통과");

    await card.getByRole("button", { name: "문서에 적용" }).click();
    await expect(page.getByText("제안을 문서에 적용했습니다.")).toBeVisible();
    await expectPhase(page, "검증 통과");

    // 저장하지 않은 채로 돌려 본다. 새 문서를 떠나므로 이탈 확인이 뜨면 나간다.
    await expect(backtest(page)).toBeEnabled();
    await backtest(page).click();
    const leaveGuard = page.getByRole("button", { name: "나가기" });
    const runStatus = page.getByRole("status", { name: "실행 상태" });
    await expect(leaveGuard.or(runStatus)).toBeVisible({ timeout: 60_000 });
    if (await leaveGuard.isVisible()) await leaveGuard.click();

    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    await expect(runStatus).toContainText("completed", { timeout: 120_000 });
    await expect(
      page.getByRole("article", { name: "백테스트 결과" }),
    ).toBeVisible({ timeout: 120_000 });
  },
);
