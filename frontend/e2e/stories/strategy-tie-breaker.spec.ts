import { expect, test } from "@playwright/test";
import type { StrategyTraceResponse } from "../../src/shared/api/generated";
import { fillRunEnvironment, openEditor, replaceSource } from "../workbench-helpers";

// 이 검사는 동점 설정 UI의 계약이다. 빈 문서에서 아이디어를 만드는 graph-only 검사를 대용하지 않는다.
test("주 점수가 같을 때 선언한 보조 팩터와 방향으로 선정한다", { tag: ["@story", "@US-CS-03"] }, async ({ page }) => {
  await openEditor(page, "/research/strategies/new");
  await replaceSource(page, `schema_version: "1.2"
title: "보조 순위 설정 검증"
factors:
  - factor_id: primary
    label: "주 점수"
    direction: high
    graph:
      nodes:
        - {node_id: price, kind: field, field_id: price.adj_close}
        - {node_id: fixed, kind: binary, operator: divide, left_node_id: price, right_node_id: price}
      output_node_id: fixed
  - factor_id: auxiliary
    label: "비교할 값"
    direction: high
    graph:
      nodes:
        - {node_id: price, kind: field, field_id: price.adj_close}
      output_node_id: price
portfolio:
  selection_count: 2
risk:
  max_name_weight: 1
  max_sector_weight: 1
`);
  await page.getByRole("tab", { name: "그래프", exact: true }).click();
  const pipeline = page.getByRole("region", { name: "전략 파이프라인" });
  await pipeline.getByRole("combobox", { name: "동점 해소 팩터", exact: true }).selectOption("auxiliary");
  await fillRunEnvironment(page);
  const preview = page.getByRole("region", { name: "선정 미리보기" });
  await preview.getByRole("combobox", { name: "팩터", exact: true }).selectOption("auxiliary");
  const refresh = async () => {
    const response = page.waitForResponse((response) => response.url().endsWith("/api/v1/strategies/debug/trace") && response.request().method() === "POST");
    await preview.getByRole("button", { name: "미리보기 새로고침", exact: true }).click();
    const received = await response;
    expect(received.ok()).toBeTruthy();
    return await received.json() as StrategyTraceResponse;
  };
  const high = await refresh();
  const values = high.factor_preview.top;
  expect(values).toHaveLength(3);
  expect(high.summary?.targets.map((row) => row.position.security_id)).toEqual(values.slice(0, 2).map((row) => row.security_id));
  await pipeline.getByRole("combobox", { name: "동점 해소 방향", exact: true }).selectOption("low");
  await expect(preview.getByText("이전 요청 · 새로고침 필요")).toBeVisible();
  const low = await refresh();
  expect(low.spec_hash).not.toBe(high.spec_hash);
  expect(low.summary?.targets.map((row) => row.position.security_id)).toEqual([...values].reverse().slice(0, 2).map((row) => row.security_id));
  expect(low.summary?.targets.map((row) => row.position.composite_score)).toEqual(high.summary?.targets.map((row) => row.position.composite_score));
  await expect(preview.getByRole("table", { name: "선정 종목" })).toBeVisible();
  for (const width of [1440, 640, 360]) {
    await page.setViewportSize({ width, height: 1000 });
    await pipeline.getByRole("combobox", { name: "동점 해소 방향", exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: test.info().outputPath(`recipe-tie-${width}.png`) });
  }
});
