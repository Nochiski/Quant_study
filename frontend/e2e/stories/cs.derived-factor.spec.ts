/**
 * 김철수의 스토리 e2e — 원천 필드를 2차 가공한 파생 팩터를 정의하고 기존 팩터와 결합한다(US-CS-02),
 * 그 계산을 실행 계획과 중간값 추적으로 검증한다(US-CS-03).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/cs.md`다. 김철수는 YAML로 팩터
 * 그래프를 직접 적는 사용자라 파생 팩터를 편집기에 입력하는 것부터 시작한다. 팩터 값과 공개일의 계산은
 * backend `FactorGraph` 평가가 소유하므로, 여기서는 값을 다시 계산하지 않고 화면에 계산 경로와 결과가
 * 보이는지만 본다.
 */
import { expect, test } from "@playwright/test";

import {
  backtest,
  expectPhase,
  GOLDEN,
  mustReplace,
  openEditor,
  replaceSource,
  saveAndWaitForRevision,
} from "../workbench-helpers";

/**
 * 장부가 대비 시가총액(book-to-market): 자본총계 ÷ 시가총액을 같은 날 종목 사이에서 z-score로 바꾼다.
 * 모멘텀(비중 0.6)과 이 파생 팩터(비중 0.4)를 합성 점수로 결합한다. 결측 처리는 실행 설정의 몫이라
 * (제품 방향, lang2 P2-02) 팩터에 `missing_policy`를 적지 않고 기본값에 맡긴다.
 */
const DERIVED_FACTOR = `  - factor_id: book_to_market
    label: "장부가/시가총액"
    direction: high
    weight: 0.4
    graph:
      nodes:
        - kind: field
          node_id: book
          field_id: financial.book_equity
        - kind: field
          node_id: cap
          field_id: price.market_cap
        - kind: binary
          node_id: btm
          operator: divide
          left_node_id: book
          right_node_id: cap
        - kind: cross_sectional
          node_id: btm_z
          operator: zscore
          input_node_id: btm
      output_node_id: btm_z
`;
const SECURITY_IDS = ["sec-005930-1", "sec-000660-1", "sec-035420-1"];

test(
  "US-CS-02 두 원천 필드를 나눈 파생 팩터를 모멘텀과 결합해 계획·추적을 확인하고 백테스트한다",
  { tag: ["@story", "@US-CS-02", "@US-CS-03"] },
  async ({ page }) => {
    test.setTimeout(180_000);
    const source = mustReplace(
      mustReplace(GOLDEN, "퀄리티 모멘텀", "US-CS-02 파생 팩터"),
      "portfolio:\n",
      `${DERIVED_FACTOR}portfolio:\n`,
    );
    await openEditor(page, "/research/strategies/new");
    await replaceSource(page, source);
    await expectPhase(page, "검증 통과");
    await saveAndWaitForRevision(page, 1);

    // 중간 결과: 파생 팩터의 마지막 노드를 골라 종목 셋을 추적한다.
    await page
      .getByRole("textbox", { name: "종목 ID", exact: true })
      .fill(SECURITY_IDS.join(", "));
    await page
      .getByRole("combobox", { name: "팩터", exact: true })
      .selectOption("book_to_market");
    await page
      .getByRole("combobox", { name: "노드", exact: true })
      .selectOption("btm_z");
    await page.getByRole("button", { name: "추적 실행" }).click();
    await expect(page.getByLabel("추적 재현 정보")).toBeVisible({
      timeout: 60_000,
    });

    // 원시 데이터: 파생 팩터가 읽은 두 원천 필드가 공개일과 함께 보인다.
    await page.getByRole("tab", { name: "원시 데이터" }).click();
    const raw = page.getByRole("tabpanel", { name: "원시 데이터" });
    await expect(
      raw.getByText("financial.book_equity", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      raw.getByText("price.market_cap", { exact: true }).first(),
    ).toBeVisible();

    // 선택 노드: 표준화한 파생 팩터 값이 종목마다 한 줄씩 있다.
    await page.getByRole("tab", { name: "선택 노드" }).click();
    const node = page.getByRole("tabpanel", { name: "선택 노드" });
    for (const securityId of SECURITY_IDS)
      await expect(
        node.getByRole("row").filter({ hasText: securityId }),
      ).toContainText("btm_z");

    // 실행 계획: 나눗셈과 횡단면 표준화 단계가 원천 필드와 함께 계획에 있다.
    await page.getByRole("tab", { name: "실행 계획" }).click();
    const plan = page.getByRole("tabpanel", { name: "실행 계획" });
    await expect(
      plan.getByRole("row").filter({ hasText: "btm_z" }).first(),
    ).toContainText("zscore");
    await expect(
      plan.getByRole("row").filter({ hasText: "divide" }).first(),
    ).toContainText("btm");

    // 결합한 전략이 끝까지 돈다.
    await expect(backtest(page)).toBeEnabled();
    await backtest(page).click();
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
