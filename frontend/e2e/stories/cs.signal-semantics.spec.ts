/** 실제 HTTP 계산으로 김철수의 정규화·횡단면 필터·역위험가중을 검증한다. */
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import type {
  CompiledDocument,
  StrategyTraceResponse,
} from "../../src/shared/api/generated";
import { waitForSettledDocument } from "../editor-helpers";
import { IDEAS_API_PREFIX } from "../runtime";
import {
  BACKEND,
  expectPhase,
  fillRunEnvironment,
  openEditor,
  openTraceWorkspace,
  openTraceSource,
  openEditorWorkspace,
  replaceSource,
  upgradeFromBanner,
} from "../workbench-helpers";

const fixture = (name: string) =>
  readFileSync(
    new URL(
      `../../../backend/tests/fixtures/strategy_documents/${name}`,
      import.meta.url,
    ),
    "utf8",
  );
const three = ["sec-005930-1", "sec-000660-1", "sec-035420-1"];
const hundred = [
  ...three,
  ...Array.from({ length: 97 }, (_, i) => `sec-${900004 + i}-1`),
];
const environment = {
  start: "2021-01-01",
  end: "2021-12-31",
  universe_id: "krx.common-stock",
};
const pipeline = (page: Page) =>
  page.getByRole("region", { name: "전략 파이프라인" });
/** 정규화·거르기·위험 설정은 선택 노드의 문맥을 명시적으로 나와 전략 전체에서 편집한다. */
const openStrategyComposition = async (page: Page) => {
  await openEditorWorkspace(page);
  const graphTab = page.getByRole("tab", { name: "그래프", exact: true });
  await graphTab.click();
  await expect(graphTab).toHaveAttribute("aria-selected", "true");
  const back = page.getByRole("button", { name: "전략 구성으로 돌아가기", exact: true });
  if (await back.isVisible()) await back.click();
  await expect(pipeline(page)).toBeVisible();
};
const number = (value: number) =>
  new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 }).format(value);
const linked = (page: Page, id: string) =>
  page
    .getByRole("list", { name: "연결 추적", exact: true })
    .getByRole("listitem", { name: id, exact: true });
const useHundred = async (page: Page) => {
  // 응답은 만들지 않는다. 기존 합성 원시 데이터 위에서 실제 compile/evaluate가 돈다.
  await page.route(
    (url) =>
      url.origin === new URL(BACKEND).origin &&
      url.pathname.startsWith("/api/v1/"),
    async (route) => {
      const url = new URL(route.request().url());
      url.pathname = IDEAS_API_PREFIX + url.pathname;
      await route.continue({ url: url.toString() });
    },
  );
};
const load = async (page: Page, source: string) => {
  await openEditor(page, "/research/strategies/new");
  await replaceSource(page, source);
  await waitForSettledDocument(page);
  await openStrategyComposition(page);
  await fillRunEnvironment(page, {}, environment);
};
const trace = async (page: Page, factorId: string, securities: string[]) => {
  await openTraceWorkspace(page);
  const controls = page.getByRole("form", { name: "전략 추적 범위" });
  await controls
    .getByRole("textbox", { name: "종목 ID", exact: true })
    .fill(securities.join(", "));
  await controls
    .getByRole("combobox", { name: "팩터", exact: true })
    .selectOption(factorId);
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/strategies/debug/trace") &&
      r.request().method() === "POST" &&
      r.request().postDataJSON().factor_id === factorId,
  );
  await controls
    .getByRole("button", { name: "추적 실행", exact: true })
    .click();
  const received = await response;
  expect(received.ok()).toBeTruthy();
  const result = (await received.json()) as StrategyTraceResponse;
  await openTraceSource(page);
  await expect(page.getByLabel("추적 재현 정보")).toBeVisible({
    timeout: 60_000,
  });
  return result;
};
const mixed = `schema_version: "1.2"
title: "단위가 다른 두 팩터"
factors:
  - factor_id: amount
    label: "주가"
    direction: high
    weight: 0.25
    graph:
      nodes:
        - {node_id: value, kind: field, field_id: price.adj_close}
      output_node_id: value
  - factor_id: ratio
    label: "공매도 비율"
    direction: high
    weight: 0.75
    graph:
      nodes:
        - {node_id: value, kind: field, field_id: short.short_balance_ratio}
      output_node_id: value
portfolio:
  selection_count: 2
risk:
  max_name_weight: 1
  max_sector_weight: 1
`;

test(
  "US-CS-05 기본 순위와 세 정규화의 기여도·합계 및 단위 경고를 추적한다",
  { tag: ["@story", "@US-CS-05"] },
  async ({ page }) => {
    test.setTimeout(180_000);
    await load(page, mixed);
    const selector = pipeline(page).getByRole("combobox", {
      name: "점수 정규화",
      exact: true,
    });
    await expect(selector).toHaveValue("rank");
    const scores = new Map<string, number[]>();
    for (const mode of ["rank", "zscore", "none"] as const) {
      await openStrategyComposition(page);
      await selector.selectOption(mode);
      await waitForSettledDocument(page);
      const problems = page.getByRole("region", { name: "문제", exact: true });
      if (mode === "none")
        await expect(problems).toContainText(
          "단위가 다른 팩터 점수를 그대로 더합니다",
        );
      else
        await expect(
          page.getByText(/단위가 다른 팩터 점수를 그대로 더합니다/u),
        ).toHaveCount(0);
      const result = await trace(page, "amount", three);
      const rows = result.target!.construction;
      expect(rows).toHaveLength(3);
      scores.set(
        mode,
        rows.map((row) => row.composite_score!),
      );
      for (const row of rows) {
        expect(row.factor_contributions).toHaveLength(2);
        let total = 0;
        for (const c of row.factor_contributions) {
          expect(c.status).toBe("ok");
          const values = rows.map(
            (item) =>
              item.factor_contributions.find(
                (v) => v.factor_id === c.factor_id,
              )!.value!,
          );
          const value = c.value!;
          // 독립 수학 oracle. 제품 계산 함수를 재사용하지 않는다.
          const mean = values.reduce((a, b) => a + b, 0) / values.length;
          const sd = Math.sqrt(
            values.reduce((sum, v) => sum + (v - mean) ** 2, 0) / values.length,
          );
          const rank =
            (values.filter((v) => v < value).length +
              (values.filter((v) => v === value).length - 1) / 2) /
            (values.length - 1);
          const normalized =
            mode === "none"
              ? value
              : mode === "rank"
                ? rank
                : (value - mean) / sd;
          const expectedWeight = c.factor_id === "amount" ? 0.25 : 0.75;
          expect(["amount", "ratio"]).toContain(c.factor_id);
          expect(c.configured_weight).toBe(expectedWeight);
          const expected = normalized * expectedWeight;
          expect(c.normalized_contribution).toBeCloseTo(expected, 8);
          total += expected;
          const visible = linked(page, row.security_id)
            .locator("span")
            .filter({ has: page.getByText(c.factor_id, { exact: true }) })
            .first();
          await expect(visible).toContainText(number(expected));
          await expect(visible).toContainText(
            `high × ${number(expectedWeight)}`,
          );
        }
        expect(row.composite_score).toBeCloseTo(total, 8);
        await expect(
          linked(page, row.security_id)
            .getByRole("listitem")
            .filter({ hasText: "4 합성 점수" }),
        ).toContainText(number(total));
      }
    }
    expect(scores.get("rank")).not.toEqual(scores.get("zscore"));
    expect(scores.get("none")).not.toEqual(scores.get("rank"));
    // 그래프 안에서 두 단위를 이미 순위로 바꿨으면 none에서도 단위 경고가 없다.
    const dimensionless =
      mixed.replaceAll(
        "      output_node_id: value",
        "        - {node_id: scaled, kind: cross_sectional, operator: rank, input_node_id: value}\n      output_node_id: scaled",
      ) + "signal:\n  normalization: none\n";
    await openEditorWorkspace(page);
    await page.getByRole("tab", { name: "YAML", exact: true }).click();
    await replaceSource(page, dimensionless);
    await expectPhase(page, "검증 통과");
    await expect(
      page.getByText(/단위가 다른 팩터 점수를 그대로 더합니다/u),
    ).toHaveCount(0);
  },
);

test(
  "US-CS-05 1.1 업그레이드는 none과 원시 기여도를 보존한다",
  { tag: ["@story", "@US-CS-05"] },
  async ({ page }) => {
    test.setTimeout(180_000);
    await openEditor(page, "/research/strategies/new");
    await replaceSource(page, fixture("quality_momentum.v1_1.yaml"));
    await expectPhase(page, "구조 오류");
    const upgraded = await upgradeFromBanner(page);
    expect(upgraded.source).toContain("normalization: none");
    await openStrategyComposition(page);
    await expect(
      pipeline(page).getByRole("combobox", {
        name: "점수 정규화",
        exact: true,
      }),
    ).toHaveValue("none");
    await fillRunEnvironment(page, {}, environment);
    const result = await trace(page, "momentum", three);
    for (const row of result.target!.construction) {
      expect(row.factor_contributions).toHaveLength(1);
      expect(row.factor_contributions[0]!.status).toBe("ok");
      expect(row.composite_score).toBeCloseTo(
        row.factor_contributions[0]!.value!,
        10,
      );
    }
  },
);

test(
  "US-CS-06 100종목 상위 20%의 20개 통과·80개 순위 탈락을 구분한다",
  { tag: ["@story", "@US-CS-06"] },
  async ({ page }) => {
    test.setTimeout(180_000);
    await useHundred(page);
    await load(page, fixture("ideas/top_trading_value.yaml"));
    const rules = pipeline(page).getByRole("group", {
      name: "거르기 규칙 목록",
      exact: true,
    });
    await rules
      .getByRole("combobox", { name: "비교 방식", exact: true })
      .selectOption("top_percent");
    const threshold = rules.getByRole("spinbutton", {
      name: "기준값",
      exact: true,
    });
    await threshold.fill("0.2");
    await threshold.press("Tab");
    await waitForSettledDocument(page);
    const result = await trace(page, "momentum_60", hundred);
    expect(result.summary!.counts).toEqual({
      universe: 100,
      eligible: 20,
      missing: 0,
      eligibility_failed: 0,
      eligibility_rank_cut: 80,
    });
    const rows = result.target!.construction;
    expect(rows).toHaveLength(100);
    expect(rows.filter((row) => row.eligible)).toHaveLength(20);
    const cut = rows.filter((row) => !row.eligible);
    expect(cut).toHaveLength(80);
    for (const row of cut)
      expect(row.exclusion_reasons).toEqual(["eligibility_rank_cut"]);
    // 100행은 가상 목록이다. 대표 탈락 종목을 다시 요청해 사용자 사유도 확인한다.
    const sample = cut[0]!.security_id;
    await trace(page, "momentum_60", [sample]);
    await expect(linked(page, sample)).toContainText(
      "상위 비율·개수 밖(순위로 잘림)",
    );
  },
);

test(
  "US-CS-06 변동성은 역가중에만 쓰이고 알파가 없으면 compile을 막는다",
  { tag: ["@story", "@US-CS-06"] },
  async ({ page }) => {
    test.setTimeout(180_000);
    await useHundred(page);
    const source = fixture("ideas/inverse_volatility.yaml")
      .replace(
        "max_name_weight: 0.1",
        "max_name_weight: 1\n  max_sector_weight: 1",
      )
      .replace(
        'label: "60일 변동성"',
        'label: "60일 변동성"\n    weight: 1000',
      );
    await load(page, source);
    await pipeline(page)
      .getByRole("combobox", { name: "비중 산정", exact: true })
      .selectOption("risk");
    await waitForSettledDocument(page);
    await pipeline(page)
      .getByRole("combobox", { name: "위험 팩터", exact: true })
      .selectOption("volatility_60");
    await waitForSettledDocument(page);
    await expect(
      page.getByRole("region", { name: "문제", exact: true }),
    ).toContainText("합성 점수에서는 빠집니다");
    const result = await trace(page, "volatility_60", hundred);
    const selected = result.target!.construction.filter((row) => row.selected);
    expect(selected).toHaveLength(20);
    const volatility = new Map(
      result.trace.rows
        .filter((row) => row.node_id === "std")
        .map((row) => [row.security_id, Number(row.value)]),
    );
    const inverseTotal = selected.reduce(
      (sum, row) => sum + 1 / volatility.get(row.security_id)!,
      0,
    );
    for (const row of result.target!.construction) {
      expect(row.factor_contributions.map((c) => c.factor_id)).toEqual([
        "momentum_12_1",
      ]);
      expect(row.composite_score).toBe(
        row.factor_contributions[0]!.normalized_contribution,
      );
    }
    for (const row of selected) {
      expect(volatility.get(row.security_id)).toBeGreaterThan(0);
      expect(row.unconstrained_target_weight).toBeCloseTo(
        1 / volatility.get(row.security_id)! / inverseTotal,
        10,
      );
    }
    const sample = selected[0]!.security_id;
    await trace(page, "volatility_60", [sample]);
    const contributions = linked(page, sample)
      .getByRole("listitem")
      .filter({ hasText: "3 팩터 기여도" });
    await expect(contributions).toContainText("momentum_12_1");
    await expect(contributions).not.toContainText("volatility_60");
    await openEditorWorkspace(page);
    await page.getByRole("tab", { name: "YAML", exact: true }).click();
    const alphaOnly = source
      .replace(/  - factor_id: volatility_60[\s\S]*?(?=portfolio:)/u, "")
      .replace("  weighting: risk\n", "")
      .replace("  risk_factor_id: volatility_60\n", "");
    await replaceSource(page, alphaOnly);
    await waitForSettledDocument(page);
    await page.getByRole("tab", { name: "그래프", exact: true }).click();
    const baseline = await trace(page, "momentum_12_1", hundred);
    for (const row of result.target!.construction) {
      const alpha = baseline.target!.construction.find(
        (candidate) => candidate.security_id === row.security_id,
      )!;
      expect(row.factor_contributions).toEqual(alpha.factor_contributions);
      expect(row.composite_score).toBe(alpha.composite_score);
      expect(row.rank).toBe(alpha.rank);
      expect(row.selected).toBe(alpha.selected);
    }
    expect(
      new Set(selected.map((row) => row.constrained_target_weight)).size,
    ).toBeGreaterThan(1);
    for (const row of selected) {
      expect(row.constrained_target_weight).toBeCloseTo(
        row.unconstrained_target_weight!,
        10,
      );
      expect(
        baseline.target!.construction.find(
          (candidate) => candidate.security_id === row.security_id,
        )!.unconstrained_target_weight,
      ).toBeCloseTo(0.05, 10);
    }
    await openEditorWorkspace(page);
    await page.getByRole("tab", { name: "YAML", exact: true }).click();
    const withoutAlpha = source.replace(
      /  - factor_id: momentum_12_1[\s\S]*?(?=  - factor_id: volatility_60)/u,
      "",
    );
    expect(withoutAlpha).not.toBe(source);
    const compilation = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/strategy-documents/compile") &&
        r.request().postDataJSON().source === withoutAlpha,
    );
    await replaceSource(page, withoutAlpha);
    const compiled = (await (await compilation).json()) as CompiledDocument;
    expect(compiled.spec_hash).toBeNull();
    expect(compiled.diagnostics).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          code: "strategy.signal.no_alpha_factor",
          severity: "error",
        }),
      ]),
    );
    await expectPhase(page, "검증 오류");
    await expect(
      page.getByRole("region", { name: "문제", exact: true }),
    ).toContainText("점수를 낼 알파 팩터가 없습니다");
  },
);
