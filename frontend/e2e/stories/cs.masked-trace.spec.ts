/**
 * 김철수의 스토리 e2e — 원장이 가린 칸 때문에 결측이 된 노드 값을 추적에서 "원장이 가림"으로 본다
 * (US-CS-03, #350).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/cs.md`다. mock 어댑터의 000660
 * 2024-01-08 신용잔고는 원장이 가린 셀이다(backend `MOCK_MASKED_CREDIT`). 신용 랙이 3세션이라 01-11 에
 * 보이고, 그 셀을 품는 2세션 차이 창(01-11·01-12)은 가린 칸을 건너 결측이다. 어느 칸이 가려지는지는
 * backend 평가기가 정하므로 여기서는 화면이 그 사유를 "입력 없음" 대신 "원장이 가림"으로 보이는지만 본다.
 */
import { expect, test } from "@playwright/test";

import {
  expectPhase,
  fillRunEnvironment,
  GOLDEN,
  mustReplace,
  openEditor,
  replaceSource,
} from "../workbench-helpers";

/** 신용잔고의 2세션 차이. 결측 처리는 실행 설정의 몫이라 팩터에 적지 않는다. */
const CREDIT_FACTOR = `  - factor_id: credit_change
    label: "신용잔고 2세션 차이"
    direction: low
    weight: 0.1
    graph:
      nodes:
        - kind: field
          node_id: balance
          field_id: credit.margin_balance
        - kind: time_series
          node_id: change
          operator: delta
          input_node_id: balance
          window: 2
      output_node_id: change
`;
const SECURITY_ID = "sec-000660-1";

test(
  "US-CS-03 원장이 가린 신용잔고 칸을 건넌 노드 값과 그 원시 셀을 원장이 가림으로 본다",
  { tag: ["@story", "@US-CS-03"] },
  async ({ page }) => {
    test.setTimeout(120_000);
    const source = mustReplace(
      mustReplace(GOLDEN, "퀄리티 모멘텀", "US-CS-03 가린 칸 추적"),
      "portfolio:\n",
      `${CREDIT_FACTOR}portfolio:\n`,
    );
    await openEditor(page, "/research/strategies/new");
    await replaceSource(page, source);
    await expectPhase(page, "검증 통과");
    await fillRunEnvironment(page);

    await page.getByLabel("기준일", { exact: true }).fill("2024-01-11");
    await page
      .getByRole("textbox", { name: "종목 ID", exact: true })
      .fill(SECURITY_ID);
    await page
      .getByRole("combobox", { name: "팩터", exact: true })
      .selectOption("credit_change");
    await page
      .getByRole("combobox", { name: "노드", exact: true })
      .selectOption("change");
    await page.getByRole("button", { name: "추적 실행" }).click();
    await expect(page.getByLabel("추적 재현 정보")).toBeVisible({
      timeout: 60_000,
    });

    // 선택 노드: 2세션 차이 창이 가린 칸을 건너 결측이다 — "입력 없음"이 아니다.
    await page.getByRole("tab", { name: "선택 노드" }).click();
    const node = page.getByRole("tabpanel", { name: "선택 노드" });
    const change = node
      .getByRole("row")
      .filter({ hasText: SECURITY_ID })
      .filter({ hasText: "change" });
    // 열: 종목 · 노드 · 연산 · 입력 · 값 · 상태
    await expect(change.getByRole("cell").nth(4)).toHaveText("—");
    await expect(change.getByRole("cell").nth(5)).toHaveText(/원장이 가림$/u);

    // 원시 데이터: 그날 보인 신용잔고 셀이 원장이 가린 셀이다.
    await page.getByRole("tab", { name: "원시 데이터" }).click();
    const raw = page.getByRole("tabpanel", { name: "원시 데이터" });
    const credit = raw
      .getByRole("row")
      .filter({ hasText: SECURITY_ID })
      .filter({ hasText: "credit.margin_balance" });
    // 열: 종목 · 필드 · 값 · 공개일 · 상태
    await expect(credit.getByRole("cell").nth(4)).toHaveText(/원장이 가림$/u);

    // 다음 날(01-12): 잔고 입력은 값으로 보이지만 2세션 차이 창이 01-11 의 가린 칸을 건너므로 여전히
    // "원장이 가림"이다. 옛 화면이 "입력 없음"으로 보이던 바로 그 칸이다(#337·#350, #389 리뷰 P3-3).
    await page.getByLabel("기준일", { exact: true }).fill("2024-01-12");
    await page.getByRole("button", { name: "추적 실행" }).click();
    await page.getByRole("tab", { name: "선택 노드" }).click();
    await expect(change.getByRole("cell").nth(3)).toHaveText(/^balance=\d/u);
    await expect(change.getByRole("cell").nth(4)).toHaveText("—");
    await expect(change.getByRole("cell").nth(5)).toHaveText(/원장이 가림$/u);
  },
);
