/**
 * 한상목의 스토리 e2e — 봉인 구간과 겹치는 실행이 이유와 교정 버튼과 함께 막히고(US-SM-13), 실행 전에 이
 * 실행이 새 시도인지 알려 준다(US-SM-16).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/sm.md`다. 봉인 구간·연구 하한 날짜와
 * 새 시도 판정은 backend 가 소유한다(검증 랩 spec D1·D2). 여기서는 실제 backend 위에서 그 답이 화면에
 * 이유·교정 버튼·시도 영향 문장으로 보이는지를 본다.
 */
import { expect, test } from "@playwright/test";

import {
  backtest,
  BACKEND,
  expectPhase,
  fillRunEnvironment,
  GOLDEN,
  mustReplace,
  openEditor,
  replaceSource,
  RUN_ENVIRONMENT,
  saveAndWaitForRevision,
  strategyIdentity,
} from "../workbench-helpers";

/** 봉인 구간(2016-01-01~2019-12-31) 안의 시작일. */
const SEALED_START = "2018-01-02";

test(
  "US-SM-13 봉인 구간과 겹치는 시작일은 이유와 교정 버튼과 함께 막히고, 실행 전에 시도 수 영향을 알려 준다",
  { tag: ["@story", "@US-SM-13", "@US-SM-16"] },
  async ({ page, request }) => {
    test.setTimeout(180_000);
    await openEditor(page, "/research/strategies/new");
    await replaceSource(
      page,
      mustReplace(GOLDEN, "퀄리티 모멘텀", "US-SM-13 봉인 구간"),
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
    // 시작일만 봉인 구간 안으로 옮긴다. 저장 뒤에도 방금 고른 실행 설정이 이어진다.
    await toggle.click();
    await page.getByLabel("시작일", { exact: true }).fill(SEALED_START);
    await toggle.click();

    // 실행이 시작되지 않고, 봉인 구간과 연구 구간 시작일을 설명하는 오류와 교정 버튼이 보인다.
    await backtest(page).click();
    const rejection = page.getByRole("alert", { name: "백테스트 시작 실패" });
    await expect(rejection).toContainText(
      "2016-01-01~2019-12-31은 홀드아웃으로 봉인돼 있고 그 앞도 측정하지 않습니다.",
    );
    await expect(rejection).toContainText("시작일을 2020-01-02 이후로 옮긴 뒤");
    await expect(page).toHaveURL(
      /\/research\/strategies\/[^/]+\/revisions\/1(?:\?.*)?$/u,
    );

    // 버튼을 누르면 시작일이 2020-01-02로 바뀌고 실행할 수 있다.
    await rejection
      .getByRole("button", { name: "시작일을 2020-01-02로" })
      .click();
    await expect(rejection).toHaveCount(0);
    await toggle.click();
    await expect(page.getByLabel("시작일", { exact: true })).toHaveValue(
      "2020-01-02",
    );
    // 실행 전: 저장한 전략의 첫 결과라 새 시도로 센다(US-SM-16).
    await expect(impact).toHaveText(
      "결과가 나오면 새 시도로 셉니다. 계열 시도 수 0회 → 1회.",
    );
    await toggle.click();
    await expect(backtest(page)).toBeEnabled();
    await backtest(page).click();
    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
      "completed",
      { timeout: 120_000 },
    );

    // 같은 설정으로 돌아오면 이미 센 시도의 재확인이라 시도 수가 그대로다(US-SM-16).
    await openEditor(page, strategyUrl);
    await toggle.click();
    await expect(impact).toHaveText(
      "이미 센 시도의 재확인이라 시도 수가 늘지 않습니다. 계열 시도 수 1회 그대로.",
    );

    // 팩터 미리보기도 같은 이유로 막힌다. 이 미리보기를 부르는 화면은 아직 없어 같은 backend 에 직접
    // 묻고, backend 가 날짜까지 넣어 완성한 문장을 본다(spec D1).
    const preview = await request.post(`${BACKEND}/api/v1/factors/preview`, {
      data: {
        graph: {
          nodes: [{ node_id: "close", field_id: "price.close", kind: "field" }],
          output_node_id: "close",
        },
        as_of_start: SEALED_START,
        as_of_end: RUN_ENVIRONMENT.end,
      },
    });
    expect(preview.status()).toBe(422);
    const { detail } = (await preview.json()) as {
      detail: {
        code: string;
        validation: { issues: { code: string; message: string }[] };
      };
    };
    expect(detail.code).toBe("factor.graph.invalid");
    const issue = detail.validation.issues.find(
      (candidate) => candidate.code === "run_environment.research_window",
    );
    expect(issue?.message).toContain(
      "2016-01-01~2019-12-31은 홀드아웃 봉인 구간이고",
    );
    expect(issue?.message).toContain("시작일을 2020-01-02 이후로 옮겨라");
  },
);
