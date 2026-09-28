/**
 * 정동민의 스토리 e2e — 백테스트 결과를 AI에게 쉬운 말로 풀어 달라고 한다(US-DM-08).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다. 공급자는 backend 대본
 * adapter다. 결과 세션은 서버가 `read_backtest_result` 도구만 주고, 대본은 그 도구 목록을 보고 결과 설명
 * 대본을 고른다(`_scenarios.py`의 `result_explanation`). 대본의 숫자는 도구가 돌려준 서버 요약에서
 * 읽으므로, 화면의 총수익률 값이 답에 그대로 나오면 "서버가 이 실행을 요약해 모델에게 줬다"를 확인한
 * 것이다.
 *
 * "설명 전용"은 제안 카드가 없다는 것으로 본다. 대본은 결과 턴의 도구 목록에 `propose_strategy`가
 * 새어 들어오면 검증을 통과하는 제안(`RESULT_PROBE_TITLE`)을 시도한다. 그래서 결과 세션에 전략 도구가
 * 섞이거나 제안 게이트가 사라지면 이 화면에 카드가 뜨고 아래 단언이 깨진다(D 스택 리뷰 P3-5). 실제
 * 모델 설명의 품질은 검사하지 않는다.
 */
import { expect, test } from "@playwright/test";

import { ensureProvider } from "../assistant-helpers";
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

/** 결과 턴에 제안 도구가 새어 들어올 때 대본이 내는 제안의 제목(`_scenarios.py`의 `RESULT_PROBE_TITLE`). */
const PROBE_PROPOSAL_TITLE = "결과 화면에서 새어 나온 제안";

/** mock equity 어댑터의 종목 어휘. 벤치마크가 있어야 비교 문장이 나온다. */
const BENCHMARK = "sec-005930-1";

test(
  "US-DM-08 완료된 백테스트 결과에서 AI에게 좋은 결과인지 물으면 지표 뜻과 벤치마크 비교를 쉬운 말로 답한다",
  { tag: ["@story", "@US-DM-08"] },
  async ({ page }) => {
    test.setTimeout(240_000);
    await ensureProvider(page);
    await openEditor(page, "/research/strategies/new");
    await replaceSource(
      page,
      mustReplace(GOLDEN, "퀄리티 모멘텀", "US-DM-08 결과 묻기"),
    );
    await expectPhase(page, "검증 통과");
    await saveAndWaitForRevision(page, 1);

    // 실행 설정(기간·유니버스)은 전략 문서 밖에 있고 사용자가 정해야 실행이 열린다(P3-02).
    await fillRunEnvironment(page, { via: "band" });
    const settingsToggle = page.getByLabel("실행 설정 열기");
    await settingsToggle.click();
    await page
      .getByRole("textbox", { name: /^벤치마크 종목 ID/ })
      .fill(BENCHMARK);
    await settingsToggle.click();
    await expect(backtest(page)).toBeEnabled();
    await backtest(page).click();

    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
      "completed",
      { timeout: 120_000 },
    );
    const result = page.getByRole("article", { name: "백테스트 결과" });
    await expect(result).toBeVisible({ timeout: 120_000 });

    // 지표 이름 옆에 쉬운 한글 이름과 한 줄 뜻이 보인다.
    const highlights = result.getByRole("region", { name: "핵심 성과 지표" });
    const cell = (label: string) =>
      highlights.getByText(label, { exact: true }).locator("xpath=..");
    await expect(cell("Sharpe ratio")).toContainText(
      "샤프 비율 흔들림 한 단위당 얼마나 벌었는지입니다.",
    );
    await expect(cell("Maximum drawdown")).toContainText(
      "최대 낙폭 가장 높았던 때에서 가장 많이 떨어진 폭입니다.",
    );
    const totalReturn = (
      await cell("Total return").locator("strong").innerText()
    ).trim();
    expect(totalReturn).toMatch(/^-?\d+\.\d{2}%$/u);

    // 결과 화면에서 AI에게 묻는다.
    await page.getByRole("button", { name: "AI에게 결과 묻기" }).click();
    const panel = page.getByRole("complementary", { name: "AI 어시스턴트" });
    await expect(
      panel.getByRole("heading", { name: "AI 어시스턴트" }),
    ).toBeVisible();
    await panel
      .getByRole("textbox", { name: "어시스턴트에게 보낼 메시지" })
      .fill("이 결과 좋은 거야?");
    await panel.getByRole("button", { name: "보내기" }).click();

    await expect(panel.getByRole("status", { name: "진행 상태" })).toHaveText(
      "답변이 완료되었습니다.",
      { timeout: 60_000 },
    );
    const log = panel.getByRole("log", { name: "대화 내용" });
    await expect(log).toContainText("이 결과 좋은 거야?");
    // 답의 숫자는 서버 요약에서 왔다: 화면의 총수익률과 같다.
    await expect(log).toContainText(
      `총수익률(Total return)은 ${totalReturn}입니다.`,
    );
    await expect(log).toContainText(`같은 기간 벤치마크(${BENCHMARK})는`);
    await expect(log).toContainText(
      "위험 한 단위당 얼마나 벌었는지를 뜻합니다.",
    );
    await expect(log).toContainText(
      "가장 높았던 때에서 가장 많이 떨어진 폭입니다.",
    );
    await expect(log).toContainText(
      "과거 결과가 앞으로의 수익을 보장하지는 않습니다.",
    );
    // 결과 화면의 대화는 설명 전용이다. 문서를 바꾸는 제안 카드가 없다. 대본은 제안 도구가 새어
    // 들어오면 이 제목의 제안을 시도하므로(`_scenarios.py`의 `RESULT_PROBE_TITLE`), 게이트가 무너지면
    // 카드와 "검증 통과" 배지가 여기 나타난다.
    await expect(
      panel.getByRole("heading", { name: PROBE_PROPOSAL_TITLE }),
    ).toHaveCount(0);
    await expect(panel.getByText("검증 통과", { exact: true })).toHaveCount(0);
    await expect(
      panel.getByRole("button", { name: "문서에 적용" }),
    ).toHaveCount(0);
  },
);
