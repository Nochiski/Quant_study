/**
 * 한상목의 스토리 e2e — 2020년 이전 시작일도 막히지 않고 실행되고(US-SM-13), 실행 전에 이 실행이 새 시도인지
 * 알려 준다(US-SM-16).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/sm.md`다. 홀드아웃은 실험마다 사전등록
 * 문서로 관리하고 앱은 구간을 막지 않는다(2026-11-05 사용자 결정). 새 시도 판정은 backend 가 소유한다(검증 랩
 * spec D2). 여기서는 실제 backend 위에서 예전 봉인 구간의 시작일이 그대로 실행되고, 그 답이 시도 영향 문장으로
 * 보이는지를 본다.
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
  strategyIdentity,
} from "../workbench-helpers";

/** 2016-01-01~2019-12-31(2026-11-04 개봉한 옛 봉인 구간) 안의 시작일. */
const PAST_START = "2018-01-02";

test(
  "US-SM-13 2020년 이전 시작일도 막히지 않고 실행되며, 실행 전에 시도 수 영향을 알려 준다",
  { tag: ["@story", "@US-SM-13", "@US-SM-16"] },
  async ({ page }) => {
    test.setTimeout(180_000);
    await openEditor(page, "/research/strategies/new");
    await replaceSource(
      page,
      mustReplace(GOLDEN, "퀄리티 모멘텀", "US-SM-13 과거 구간"),
    );
    await expectPhase(page, "검증 통과");
    await fillRunEnvironment(page);
    // 저장한 적 없는 전략의 실행은 시도 수에 들지 않는다(US-SM-16).
    const toggle = page.getByLabel("실행 설정 열기");
    const impact = page.getByRole("status", { name: "시도 영향" });
    await toggle.click();
    await expect(impact).toHaveText(
      "저장한 적 없는 전략이라 이 실행은 시도 수에 들지 않습니다. 리비전을 저장한 뒤 실행하면 셉니다.",
    );
    await toggle.click();
    await saveAndWaitForRevision(page, 1);
    const { strategyId } = strategyIdentity(page);
    const strategyUrl = `/research/strategies/${strategyId}/revisions/1`;
    // 시작일만 2020년 이전으로 옮긴다. 저장 뒤에도 방금 고른 실행 설정이 이어진다.
    await toggle.click();
    await page.getByLabel("시작일", { exact: true }).fill(PAST_START);
    // 실행 전: 저장한 전략의 첫 결과라 새 시도로 센다(US-SM-16).
    await expect(impact).toHaveText(
      "결과가 나오면 새 시도로 셉니다. 계열 시도 수 0회 → 1회. 시도가 늘면 같은 결과의 시도 횟수 보정 통계(DSR)가 낮아질 수 있어요.",
    );
    await toggle.click();

    // 거절 없이 실행이 시작되고 끝난다.
    await expect(backtest(page)).toBeEnabled();
    await backtest(page).click();
    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
      "완료",
      { timeout: 120_000 },
    );

    // 같은 설정으로 돌아오면 이미 센 시도의 재확인이라 시도 수가 그대로다(US-SM-16).
    await openEditor(page, strategyUrl);
    await toggle.click();
    await expect(page.getByLabel("시작일", { exact: true })).toHaveValue(
      PAST_START,
    );
    await expect(impact).toHaveText(
      "이미 센 시도의 재확인이라 시도 수가 늘지 않습니다. 계열 시도 수 1회 그대로.",
    );
  },
);
