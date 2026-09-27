/**
 * 정동민의 스토리 e2e — 기간·유니버스·비용을 전략 밖 실행 설정에서 정하고, 기간만 바꿔 다시 돌린다
 * (US-DM-05).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다. 실행 설정 패널의
 * 필드·기본값·범위가 실행 설정 스키마에서 오는지는 `features/run-backtest` 단위 테스트가 잠근다. 여기서는
 * 실제 backend 위에서 (1) 서버 기본값과 빈 기간·유니버스, (2) 요약 띠의 "전략 문서 밖" 문구, (3) 기간만
 * 바꾼 두 실행이 같은 전략 revision·같은 strategy hash 에 서로 다른 실행 기록을 남기는지를 본다.
 */
import { expect, test, type Page } from "@playwright/test";

import { getBacktestResult } from "../../src/shared/api/generated";
import {
  apiClient,
  backtest,
  expectPhase,
  fillRunEnvironment,
  GOLDEN,
  mustReplace,
  openEditor,
  replaceSource,
  requireData,
  RUN_ENVIRONMENT,
  saveAndWaitForRevision,
  strategyIdentity,
} from "../workbench-helpers";

/** 두 번째 실행에서 바꾸는 시작일. 첫 실행과 다른 기간이면 된다. */
const LATER_START = "2023-01-02";

/** 백테스트를 시작해 끝까지 기다리고, 결과 화면의 실행 기록에서 실행 기간 줄을 읽는다. */
const runAndReadPeriod = async (page: Page): Promise<string> => {
  await expect(backtest(page)).toBeEnabled();
  await backtest(page).click();
  await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
  await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
    "completed",
    { timeout: 120_000 },
  );
  const drawer = page.getByRole("group", {
    name: "Manifest · 데이터 경고 · 재현성 정보",
  });
  await expect(drawer).toBeVisible({ timeout: 120_000 });
  await drawer.getByText("Manifest · 데이터 경고 · 재현성 정보").click();
  const period = drawer
    .getByText("실행 기간", { exact: true })
    .locator("xpath=..");
  await expect(period).toBeVisible();
  return new URL(page.url()).pathname.split("/").at(-1)!;
};

test(
  "US-DM-05 실행 설정에서 기간만 바꿔 다시 돌려도 전략은 그대로이고 실행 기록에 바꾼 기간이 남는다",
  { tag: ["@story", "@US-DM-05"] },
  async ({ page }) => {
    test.setTimeout(300_000);
    await openEditor(page, "/research/strategies/new");
    await replaceSource(
      page,
      mustReplace(GOLDEN, "퀄리티 모멘텀", "US-DM-05 실행 설정"),
    );
    await expectPhase(page, "검증 통과");
    await saveAndWaitForRevision(page, 1);
    const { strategyId } = strategyIdentity(page);
    const strategyUrl = `/research/strategies/${strategyId}/revisions/1`;

    // 요약 띠: 지금 값과 "전략 문서 밖"이라는 문장. 기간·유니버스는 서버가 기본값을 주지 않는다.
    const summary = page.getByRole("region", { name: "실행 설정 요약" });
    await expect(summary).toContainText("전략 문서 밖의 값입니다");
    await expect(summary).toContainText(
      "기간과 유니버스가 정해지지 않았습니다",
    );
    await expect(backtest(page)).toBeDisabled();

    // 실행 설정: 나머지 칸은 서버(실행 설정 스키마)가 준 기본값으로 채워져 있다.
    const toggle = page.getByLabel("실행 설정 열기");
    await toggle.click();
    await expect(page.getByRole("combobox", { name: "시장" })).toHaveValue(
      "KRX",
    );
    await expect(
      page.getByRole("combobox", { name: "체결 시점" }),
    ).toHaveValue("next_open");
    await expect(
      page.getByRole("spinbutton", { name: "수수료(bp)" }),
    ).toHaveValue("15");
    await expect(
      page.getByRole("spinbutton", { name: "슬리피지(bp)" }),
    ).toHaveValue("10");
    await expect(
      page.getByRole("spinbutton", { name: "참여율(비율)" }),
    ).toHaveValue("0.1");
    await expect(
      page.getByRole("combobox", { name: "결측 처리" }),
    ).toHaveValue("drop");
    await expect(page.getByLabel("시작일", { exact: true })).toHaveValue("");
    await toggle.click();

    await fillRunEnvironment(page);
    await expect(summary).toContainText(RUN_ENVIRONMENT.start);
    const firstRun = await runAndReadPeriod(page);
    await expect(
      page.getByText("실행 기간", { exact: true }).locator("xpath=.."),
    ).toContainText(`${RUN_ENVIRONMENT.start} → ${RUN_ENVIRONMENT.end}`);

    // 같은 전략으로 돌아와 기간만 바꾼다. 마지막 사용값이 이 전략의 실행 설정을 다시 채운다.
    await openEditor(page, strategyUrl);
    await expect(summary).toContainText(RUN_ENVIRONMENT.universe_id);
    await toggle.click();
    await page.getByLabel("시작일", { exact: true }).fill(LATER_START);
    await toggle.click();
    await expect(summary).toContainText(LATER_START);
    await expectPhase(page, "저장됨");
    const secondRun = await runAndReadPeriod(page);
    await expect(
      page.getByText("실행 기간", { exact: true }).locator("xpath=.."),
    ).toContainText(`${LATER_START} → ${RUN_ENVIRONMENT.end}`);

    // 전략 문서는 그대로다: 두 실행 모두 같은 revision·같은 strategy hash 이고 실행 설정만 다르다.
    const [first, second] = await Promise.all(
      [firstRun, secondRun].map(async (runId) =>
        requireData(
          (
            await getBacktestResult({
              client: apiClient,
              path: { run_id: runId },
            })
          ).data,
          `get backtest result ${runId}`,
        ),
      ),
    );
    expect(first!.manifest.strategy_provenance).toMatchObject({
      kind: "saved_revision",
      strategy_id: strategyId,
      revision: 1,
    });
    expect(second!.manifest.strategy_provenance).toMatchObject({
      kind: "saved_revision",
      strategy_id: strategyId,
      revision: 1,
    });
    expect(second!.manifest.strategy_hash).toBe(first!.manifest.strategy_hash);
    expect(second!.manifest.environment.start).toBe(LATER_START);
    expect(second!.manifest.environment_hash).not.toBe(
      first!.manifest.environment_hash,
    );
  },
);
