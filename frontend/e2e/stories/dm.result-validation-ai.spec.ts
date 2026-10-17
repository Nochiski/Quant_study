/**
 * 정동민의 스토리 e2e — AI 결과 설명이 검증 카드의 근거와 그 시점을 함께 말한다(US-DM-12).
 *
 * 공급자는 backend 대본 adapter다. 대본은 결과 요약의 `validation` 칸에서 PSR·DSR·N·조회 시점을
 * 읽어 옮기므로(`_scenarios.py`의 `_validation_parts`), 검증 카드가 읽은 서버 값과 같은 수치가 답에
 * 나오면 "서버가 카드와 같은 근거를 모델에게 줬다"를 확인한 것이다. 후보 선택 당시 스냅숏의 문장은
 * 실험에서 고른 실행에만 나온다 — 단일 실행인 여기서는 나오지 않아야 하고, 선택 스냅숏을 시점별로
 * 나누는 갈래는 backend 테스트(`test_adapters_llm_scripted.py`·`test_assistant_result_context.py`)가
 * 본다. 실제 모델 설명의 품질은 검사하지 않는다.
 */
import { expect, test } from "@playwright/test";

import type { RunValidationReport } from "../../src/shared/api/generated";
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

/** 대본의 퍼센트 표기(`_scenarios.py`의 `_percent`, 소수 둘째 자리)와 같은 모양. */
const percent = (value: number) => `${(value * 100).toFixed(2)}%`;

test(
  "US-DM-12 AI에게 결과를 믿어도 되는지 물으면 검증 카드의 PSR·DSR 원래 수치와 조회 시점을 함께 답한다",
  { tag: ["@story", "@US-DM-12"] },
  async ({ page }) => {
    test.setTimeout(240_000);
    await ensureProvider(page);
    await openEditor(page, "/research/strategies/new");
    await replaceSource(
      page,
      mustReplace(GOLDEN, "퀄리티 모멘텀", "US-DM-12 검증 근거 묻기"),
    );
    await expectPhase(page, "검증 통과");
    await saveAndWaitForRevision(page, 1);
    await fillRunEnvironment(page);
    const response = page.waitForResponse((value) =>
      /\/api\/v1\/backtests\/[^/]+\/validation-report$/u.test(
        new URL(value.url()).pathname,
      ),
    );
    await backtest(page).click();
    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    const http = await response;
    expect(http.ok()).toBe(true);
    const report = (await http.json()) as RunValidationReport;
    // 저장한 리비전의 첫 실행이라 계열 N=1 이고 DSR 값이 있다.
    expect(report.dsr.trial_count).toBe(1);
    expect(report.dsr.value).not.toBeNull();
    expect(report.psr?.value).toEqual(expect.any(Number));
    const card = page.getByRole("region", { name: "결과 검증", exact: true });
    await expect(card).toBeVisible();

    await page.getByRole("button", { name: "AI에게 결과 묻기" }).click();
    const panel = page.getByRole("complementary", { name: "AI 어시스턴트" });
    await panel
      .getByRole("textbox", { name: "어시스턴트에게 보낼 메시지" })
      .fill("이 결과 믿어도 돼?");
    await panel.getByRole("button", { name: "보내기" }).click();
    await expect(panel.getByRole("status", { name: "진행 상태" })).toHaveText(
      "답변이 완료되었습니다.",
      { timeout: 60_000 },
    );
    const log = panel.getByRole("log", { name: "대화 내용" });

    // 원래 수치는 검증 카드가 읽은 서버 값과 같다. PSR 은 미래 확률이 아니라고 함께 말한다.
    await expect(log).toContainText(
      `PSR(probabilistic_sharpe)은 ${percent(report.psr?.value ?? Number.NaN)}입니다.`,
    );
    await expect(log).toContainText("앞으로 벌 확률은 아닙니다");
    // DSR 은 조회 시점을 밝힌 지금 원장 기준이다(조회 시각은 AI 턴이 읽은 시각이라 카드와 다를 수 있다).
    await expect(log).toContainText(
      new RegExp(
        `지금 계열 원장\\([^)]+ 조회\\) 기준 시도 수 N=1, DSR은 ${percent(report.dsr.value ?? Number.NaN).replace(".", "\\.")}입니다\\.`,
        "u",
      ),
    );
    // 실험에서 고른 실행이 아니므로 선택 당시 기록을 지어내지 않는다.
    await expect(log).not.toContainText("후보로 고를 당시");
    await expect(log).toContainText(
      "과거 결과가 앞으로의 수익을 보장하지는 않습니다.",
    );
  },
);
