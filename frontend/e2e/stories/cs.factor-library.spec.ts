/**
 * 김철수의 스토리 e2e — 파생 팩터를 저장해 두고 다른 전략에서 다시 쓴다(US-CS-08, 보드 04c).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/cs.md`다. 라이브러리는 참조가 아니라
 * 복사라서, 라이브러리에 새 버전을 저장해도 저장된 전략 리비전의 의미 해시는 그대로이고 편집기가 새 버전을
 * 알려 사용자가 바꿀지 고른다. 새 버전은 실제 backend API 로 저장해 화면 밖 변경을 흉내 낸다.
 */
import { expect, test } from "@playwright/test";

import {
  addLibraryFactorVersion,
  getStrategyDocument,
  listLibraryFactors,
} from "../../src/shared/api/generated";
import { currentSource, replaceSource } from "../editor-helpers";
import {
  apiClient,
  expectPhase,
  openEditor,
  requireData,
  saveAndWaitForRevision,
  strategyIdentity,
} from "../workbench-helpers";

test(
  "US-CS-08 파생 팩터를 내 팩터로 저장해 다른 전략에서 꺼내 쓰고, 새 버전은 알림을 보고 바꾼다",
  { tag: ["@story", "@US-CS-08"] },
  async ({ page }) => {
    test.setTimeout(150_000);
    const suffix = `${Date.now()}`;
    const key = `lib_mean_${suffix}`;
    const label = `종가 평균 ${suffix}`;
    const document = (window: number) =>
      [
        'schema_version: "1.2"',
        `title: 라이브러리 원본 ${suffix}`,
        "factors:",
        `  - factor_id: ${key}`,
        `    label: ${label}`,
        "    direction: high",
        "    graph:",
        "      nodes:",
        "        - node_id: close",
        "          kind: field",
        "          field_id: price.adj_close",
        "        - node_id: mean",
        "          kind: time_series",
        "          operator: mean",
        "          input_node_id: close",
        `          window: ${window}`,
        "      output_node_id: mean",
        "",
      ].join("\n");

    // 1. 파생 팩터가 있는 전략을 저장하고, 팩터 카드에서 내 팩터로 저장한다.
    await openEditor(page, "/research/strategies/new");
    await replaceSource(page, document(20));
    await expectPhase(page, "검증 통과");
    await saveAndWaitForRevision(page, 1);
    const { strategyId } = strategyIdentity(page);
    await page.getByRole("tab", { name: "그래프", exact: true }).click();
    await page.getByRole("button", { name: `${label} · 내 팩터로 저장` }).click();
    await page.getByRole("button", { name: "저장", exact: true }).click();
    await expect(page.getByText(`내 팩터 "${label}"에 저장했어요 (v1).`)).toBeVisible();

    // 2. 내 팩터 화면에 보인다.
    await page.getByRole("link", { name: "내 팩터" }).click();
    await expect(page.getByRole("heading", { name: label })).toBeVisible();

    // 3. 다른 새 전략의 안내에서 내 팩터를 꺼내 쓴다.
    await page.goto("/research/strategies/new?guide=true&step=signal.factors");
    await page.getByRole("button", { name: "내 팩터", exact: true }).click();
    await page.getByRole("button", { name: `${label} · 담기` }).click();
    const preview = page.getByRole("complementary", { name: "전략 문서 미리보기" });
    await expect(preview).toContainText(`factor_id: ${key}`);

    // 4. 라이브러리에 새 버전이 생겨도 저장된 리비전의 의미 해시는 그대로이고, 편집기가 새 버전을 알린다.
    const before = requireData(
      (
        await getStrategyDocument({
          client: apiClient,
          path: { strategy_id: strategyId, revision: 1 },
        })
      ).data,
      "strategy document",
    ).spec_hash;
    const entry = requireData(
      (await listLibraryFactors({ client: apiClient })).data,
      "library",
    ).items.find((item) => item.key === key);
    expect(entry).toBeDefined();
    const stored = entry!.versions[0]!.factor;
    const graph = stored.graph as { nodes: Record<string, unknown>[] };
    const response = await addLibraryFactorVersion({
      client: apiClient,
      path: { library_id: entry!.library_id },
      body: {
        expected_latest: 1,
        parameters: [],
        factor: {
          ...stored,
          graph: {
            ...graph,
            nodes: graph.nodes.map((node) =>
              node.node_id === "mean" ? { ...node, window: 60 } : node,
            ),
          },
        },
      },
    });
    expect(response.response?.status).toBe(201);
    const after = requireData(
      (
        await getStrategyDocument({
          client: apiClient,
          path: { strategy_id: strategyId, revision: 1 },
        })
      ).data,
      "strategy document",
    ).spec_hash;
    expect(after).toBe(before);

    await page.goto(`/research/strategies/${strategyId}/revisions/1?view=graph`);
    const notice = page.getByRole("region", { name: "내 팩터 새 버전" });
    await expect(notice).toContainText(`"${label}"은(는) 내 팩터 "${label}" v1를 복사한 것이고 v2이 있어요.`);
    await notice.getByRole("button", { name: "v2로 바꾸기" }).click();
    await page.getByRole("tab", { name: "YAML", exact: true }).click();
    await expect.poll(() => currentSource(page)).toContain("window: 60");
  },
);
