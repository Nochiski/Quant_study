/**
 * 김철수의 스토리 e2e — 빈 칸을 정한 값으로 채우는 `fill` 노드를 추적에서 본다(US-CS-14, #608).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/cs.md`다. 신용잔고를 자기 자신과의
 * 차이(늘 0)로 나눠 모든 칸을 빈 값으로 만든 뒤 `fill` 로 0 을 채운다. mock 의 000660 2024-01-08 신용잔고는
 * 원장이 가린 셀이고 신용 랙이 3세션이라 01-11 에 보인다(cs.masked-trace.spec.ts 와 같은 셀). 어느 칸을
 * 채우고 어느 칸을 가리는지는 backend 평가기가 정하므로, 여기서는 화면이 그 결과를 그대로 보이는지만 본다.
 */
import { expect, test } from "@playwright/test";

import {
  expectPhase,
  fillRunEnvironment,
  GOLDEN,
  mustReplace,
  openEditor,
  openTraceWorkspace,
  openTraceSource,
  replaceSource,
} from "../workbench-helpers";

/** 신용잔고 ÷ (신용잔고 − 신용잔고) — 0 으로 나눠 늘 빈 값이고, `fill` 이 그 칸을 0 으로 채운다. */
const FILL_FACTOR = `  - factor_id: credit_filled
    label: "빈 칸을 0으로 채운 신용잔고 비율"
    direction: low
    weight: 0.1
    graph:
      nodes:
        - kind: field
          node_id: balance
          field_id: credit.margin_balance
        - kind: binary
          node_id: zero
          operator: subtract
          left_node_id: balance
          right_node_id: balance
        - kind: binary
          node_id: ratio
          operator: divide
          left_node_id: balance
          right_node_id: zero
        - kind: fill
          node_id: filled
          input_node_id: ratio
          fill_value: 0
      output_node_id: filled
`;
const SECURITY_ID = "sec-000660-1";

test(
  "US-CS-14 빈 칸은 채운 값으로, 원장이 가린 칸은 채우지 않은 채로 추적에서 본다",
  { tag: ["@story", "@US-CS-14"] },
  async ({ page }) => {
    test.setTimeout(120_000);
    const source = mustReplace(
      mustReplace(GOLDEN, "퀄리티 모멘텀", "US-CS-14 빈 칸 채우기"),
      "portfolio:\n",
      `${FILL_FACTOR}portfolio:\n`,
    );
    await openEditor(page, "/research/strategies/new");
    await replaceSource(page, source);
    await expectPhase(page, "검증 통과");
    await fillRunEnvironment(page);
    await openTraceWorkspace(page);

    const traceControls = page.getByRole("form", { name: "전략 추적 범위" });
    await traceControls.getByLabel("기준일", { exact: true }).fill("2024-01-12");
    await page
      .getByRole("textbox", { name: "종목 ID", exact: true })
      .fill(SECURITY_ID);
    await page
      .getByRole("combobox", { name: "팩터", exact: true })
      .selectOption("credit_filled");
    await page
      .getByRole("combobox", { name: "노드", exact: true })
      .selectOption("filled");
    await page.getByRole("button", { name: "추적 실행" }).click();
    await openTraceSource(page);
    await expect(page.getByLabel("추적 재현 정보")).toBeVisible({
      timeout: 60_000,
    });

    // 01-12: 나눗셈은 0 으로 나눠 빈 값이지만 `fill` 이 0 을 채워 정상 값이다.
    await page.getByRole("tab", { name: "선택 노드" }).click();
    const node = page.getByRole("tabpanel", { name: "선택 노드" });
    const filled = node
      .getByRole("row")
      .filter({ hasText: SECURITY_ID })
      .filter({ hasText: "filled" });
    // 열: 종목 · 노드 · 연산 · 입력 · 값 · 상태
    await expect(filled.getByRole("cell").nth(3)).toHaveText(/^ratio=—/u);
    await expect(filled.getByRole("cell").nth(4)).toHaveText(/^0(\.0+)?$/u);
    await expect(filled.getByRole("cell").nth(5)).toHaveText(/계산됨$/u);

    // 01-11: 그날 보인 신용잔고 칸은 원장이 가린 셀이라 `fill` 도 채우지 않는다.
    await traceControls.getByLabel("기준일", { exact: true }).fill("2024-01-11");
    await page.getByRole("button", { name: "추적 실행" }).click();
    await page.getByRole("tab", { name: "선택 노드" }).click();
    await expect(filled.getByRole("cell").nth(4)).toHaveText("—");
    await expect(filled.getByRole("cell").nth(5)).toHaveText(/원장이 가림$/u);
  },
);
