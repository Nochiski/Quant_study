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

/** 결과 화면 실행 기록의 "실행 설정" 묶음에서 칸 하나의 줄. 칸 이름은 실행 설정 스키마 라벨이다. */
const recordedRow = (page: Page, label: string) =>
  page
    .getByRole("group", { name: "실행 설정", exact: true })
    .getByText(label, { exact: true })
    .locator("xpath=..");

/** 백테스트를 시작해 끝까지 기다리고, 결과 화면의 실행 기록에서 시작일 줄을 연다. */
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
  await expect(recordedRow(page, "시작일")).toBeVisible();
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
      "실행 설정에서 시작일·종료일·유니버스 칸을 채우세요.",
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
      page.getByRole("spinbutton", { name: "수수료 (bp)" }),
    ).toHaveValue("15");
    await expect(
      page.getByRole("spinbutton", { name: "슬리피지 (bp)" }),
    ).toHaveValue("10");
    // 참여율은 스키마의 표시 단위(%)로 보인다. 요청에는 비율 0.1 로 실린다.
    await expect(
      page.getByRole("spinbutton", { name: "참여율 (%)" }),
    ).toHaveValue("10");
    await expect(
      page.getByRole("combobox", { name: "결측 처리" }),
    ).toHaveValue("drop");
    await expect(page.getByLabel("시작일", { exact: true })).toHaveValue("");
    await toggle.click();

    await fillRunEnvironment(page);
    await expect(summary).toContainText(RUN_ENVIRONMENT.start);

    // 기간·유니버스를 정한 뒤 다른 칸이 틀리면 띠가 그 칸 이름과 이유를 말하고, 버튼이 그 칸으로
    // 초점을 옮긴다(DEFECT-242-01).
    await toggle.click();
    const fee = page.getByRole("spinbutton", { name: "수수료 (bp)" });
    await fee.fill("-1");
    await toggle.click();
    await expect(summary).toContainText(
      "실행 설정의 수수료 칸을 고치세요: 0bp 이상이어야 합니다.",
    );
    await expect(backtest(page)).toBeDisabled();
    await summary.getByRole("button", { name: "실행 설정 고치기" }).click();
    await expect(fee).toBeFocused();
    await fee.fill("15");
    await toggle.click();
    await expect(
      summary.getByRole("button", { name: "실행 설정 고치기" }),
    ).toHaveCount(0);

    const firstRun = await runAndReadPeriod(page);
    await expect(recordedRow(page, "시작일")).toContainText(
      RUN_ENVIRONMENT.start,
    );
    await expect(recordedRow(page, "종료일")).toContainText(
      RUN_ENVIRONMENT.end,
    );
    // 기록의 enum 은 패널과 같은 값 이름, 참여율은 표시 단위로 보인다(DEFECT-242-04).
    await expect(recordedRow(page, "결측 처리")).toContainText(
      "그 종목을 빼기",
    );
    await expect(recordedRow(page, "참여율 (%)")).toContainText("10%");

    // 같은 전략으로 돌아와 기간만 바꾼다. 마지막 사용값이 이 전략의 실행 설정을 다시 채운다.
    await openEditor(page, strategyUrl);
    await expect(summary).toContainText(RUN_ENVIRONMENT.universe_id);
    await toggle.click();
    await page.getByLabel("시작일", { exact: true }).fill(LATER_START);
    await toggle.click();
    await expect(summary).toContainText(LATER_START);
    await expectPhase(page, "저장됨");
    const secondRun = await runAndReadPeriod(page);
    await expect(recordedRow(page, "시작일")).toContainText(LATER_START);

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
