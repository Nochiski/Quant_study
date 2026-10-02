import {
  expect,
  test,
  type Locator,
  type Page,
  type Request,
} from "@playwright/test";
import { readFileSync } from "node:fs";
import {
  compileStrategyDocument,
  getBacktestResult,
  getStrategyDocumentContract,
} from "../../src/shared/api/generated";
import { createClient } from "../../src/shared/api/generated/client";
import { waitForSettledDocument } from "../editor-helpers";
import { IDEAS_API_PREFIX } from "../runtime";
import {
  BACKEND,
  backtest,
  fillRunEnvironment,
  requireData,
} from "../workbench-helpers";

// 여러 레시피 편집·compile과 실제 백테스트를 포함하므로 기존 장기 워크벤치 흐름과 같은 예산을 쓴다.
test.beforeEach(async ({ page }) => {
  test.setTimeout(180_000);
  // 실제 HTTP 앱에 경로만 돌린다. 응답·원문 주입 없이 100종목 데이터와 독립 저장소를 사용한다.
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
});

const ideasClient = createClient({ baseUrl: BACKEND + IDEAS_API_PREFIX });
const ideaEnvironment = {
  start: "2021-01-01",
  end: "2021-12-31",
  universe_id: "krx.common-stock",
};
const isCompile = (url: string) =>
  ["", IDEAS_API_PREFIX].some(
    (prefix) =>
      new URL(url).pathname === `${prefix}/api/v1/strategy-documents/compile`,
  );

const pipeline = (page: Page) =>
  page.getByRole("region", { name: "전략 파이프라인" });
const recipe = (page: Page) =>
  page.getByRole("region", { name: "팩터 레시피" });
const identifiers = /_id|_node|kind:/;

const commit = async (page: Page, field: Locator, value: string) => {
  await field.fill(value);
  await field.press("Tab");
  await waitForSettledDocument(page);
};

const start = async (page: Page, title: string) => {
  // 원문을 넣거나 YAML 탭을 열지 않는다. fixture는 마지막 기대 hash 조회에만 사용한다.
  let latestHash: string | null = null;
  let latestRequest: Request | null = null;
  page.on("request", (request) => {
    if (!isCompile(request.url())) return;
    latestRequest = request;
    latestHash = null;
  });
  page.on("response", async (response) => {
    if (!isCompile(response.url()) || !response.ok()) return;
    const result = (await response.json()) as { spec_hash: string | null };
    if (response.request() === latestRequest) latestHash = result.spec_hash;
  });
  await page.goto("/research/strategies/new");
  await expect(
    page.getByRole("tab", { name: "그래프", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await commit(
    page,
    pipeline(page).getByRole("textbox", { name: "전략 이름", exact: true }),
    title,
  );
  await commit(
    page,
    pipeline(page).getByRole("spinbutton", {
      name: "종목별 최대 목표 비중 한도",
      exact: true,
    }),
    "0.05",
  );
  return () => latestHash;
};

const factor = async (
  page: Page,
  id: string | null,
  label: string,
  direction: "high" | "low" = "high",
) => {
  const canvas = pipeline(page);
  const labels = canvas.getByRole("textbox", {
    name: "표시 이름",
    exact: true,
  });
  const previousCount = await labels.count();
  await canvas
    .getByRole("button", { name: "알파 팩터 · 항목 추가", exact: true })
    .click();
  // 새 항목이 parse 투영에 나타난 뒤 편집한다. 기존 마지막 팩터를 먼저 채우면 안 된다.
  await expect(labels).toHaveCount(previousCount + 1);
  await waitForSettledDocument(page);
  await commit(page, labels.nth(previousCount), label);
  const card = canvas.getByRole("group", { name: label, exact: true });
  if (id !== null) {
    await card
      .getByRole("button", { name: "식별자(YAML)", exact: true })
      .click();
    await commit(
      page,
      card.getByRole("textbox", { name: "팩터 이름", exact: true }),
      id,
    );
    await card
      .getByRole("button", { name: "식별자(YAML)", exact: true })
      .click();
  }
  await card
    .getByRole("combobox", { name: "선호 방향", exact: true })
    .selectOption(direction);
  await waitForSettledDocument(page);
  await card
    .getByRole("button", { name: `${label} · 레시피 열기`, exact: true })
    .click();
  await expect(recipe(page)).toBeVisible();
};

const field = async (page: Page, fieldId: string) => {
  await recipe(page)
    .getByRole("button", { name: "데이터 필드 노드 추가", exact: true })
    .click();
  await recipe(page)
    .getByRole("combobox", { name: "데이터 필드 1", exact: true })
    .selectOption(fieldId);
  await recipe(page)
    .getByRole("button", { name: "단계 반영", exact: true })
    .click();
  await waitForSettledDocument(page);
};

const period = async (
  page: Page,
  name: string,
  window: number,
  lag?: number,
) => {
  const cards = recipe(page).getByRole("article");
  const previousCount = await cards.count();
  await recipe(page)
    .getByRole("button", { name: `${name} 노드 추가`, exact: true })
    .click();
  await expect(cards).toHaveCount(previousCount + 1);
  await waitForSettledDocument(page);
  const card = cards.nth(previousCount);
  await commit(
    page,
    card.getByRole("spinbutton", { name: /집계 기간/ }),
    String(window),
  );
  if (lag !== undefined)
    await commit(
      page,
      card.getByRole("spinbutton", { name: /건너뛰는 세션/ }),
      String(lag),
    );
};

const binary = async (
  page: Page,
  name: string,
  fieldId: string,
  previousInput = "left_node_id",
) => {
  await recipe(page)
    .getByRole("button", { name: `${name} 노드 추가`, exact: true })
    .click();
  await recipe(page)
    .getByRole("combobox", { name: "앞 단계가 들어갈 입력", exact: true })
    .selectOption(previousInput);
  await recipe(page)
    .getByRole("combobox", { name: "데이터 필드 1", exact: true })
    .selectOption(fieldId);
  await recipe(page)
    .getByRole("button", { name: "단계 반영", exact: true })
    .click();
  await waitForSettledDocument(page);
};

const closeRecipe = async (page: Page) => {
  await expect(recipe(page)).not.toContainText(identifiers);
  await recipe(page)
    .getByRole("button", { name: "파이프라인으로", exact: true })
    .click();
  await expect(pipeline(page)).not.toContainText(identifiers);
};

const momentum = async (
  page: Page,
  id: string | null,
  label: string,
  window: number,
  lag?: number,
) => {
  await factor(page, id, label);
  await field(page, "price.adj_close");
  await period(page, "기간 수익률", window, lag);
  await closeRecipe(page);
};

const eligibility = async (
  page: Page,
  fieldId: string,
  operator: string,
  value: string,
) => {
  const rules = pipeline(page).getByRole("group", {
    name: "거르기 규칙 목록",
    exact: true,
  });
  await rules
    .getByRole("button", { name: "거르기 규칙 목록 · 항목 추가", exact: true })
    .click();
  await rules
    .getByRole("combobox", { name: "데이터 필드", exact: true })
    .selectOption(fieldId);
  await waitForSettledDocument(page);
  await rules
    .getByRole("combobox", { name: "비교 방식", exact: true })
    .selectOption(operator);
  await commit(
    page,
    rules.getByRole("spinbutton", { name: "기준값", exact: true }),
    value,
  );
};

const finish = async (
  page: Page,
  fixture: string,
  hashes: () => string | null,
) => {
  const source = readFileSync(
    new URL(
      `../../../backend/tests/fixtures/strategy_documents/ideas/${fixture}.yaml`,
      import.meta.url,
    ),
    "utf8",
  );
  const expected = requireData(
    (
      await compileStrategyDocument({
        client: ideasClient,
        body: { source, format: "yaml" },
      })
    ).data,
    "아이디어 정본 compile",
  );
  expect(expected.spec_hash).not.toBeNull();
  await waitForSettledDocument(page);
  await expect.poll(hashes).toBe(expected.spec_hash);
  await expect(pipeline(page)).not.toContainText(identifiers);
  await expect(
    page.getByRole("tab", { name: "그래프", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await fillRunEnvironment(page, {}, ideaEnvironment);
  await backtest(page).click();
  await page
    .getByRole("alertdialog", { name: "저장하지 않은 변경이 있습니다" })
    .getByRole("button", { name: "나가기", exact: true })
    .click();
  await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/);
  await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
    "completed",
    { timeout: 120_000 },
  );
  const runId = new URL(page.url()).pathname.split("/").at(-1)!;
  const result = requireData(
    (await getBacktestResult({ client: ideasClient, path: { run_id: runId } }))
      .data,
    "아이디어 실행 결과",
  );
  const contract = requireData(
    (await getStrategyDocumentContract({ client: ideasClient })).data,
    "아이디어 데이터 계약",
  );
  expect(result.manifest.strategy_hash).toBe(expected.spec_hash);
  expect(result.manifest.strategy_provenance.spec_hash).toBe(
    expected.spec_hash,
  );
  expect(result.manifest.data_snapshot_id).toBe(contract.contract.dataset_snapshot_id);
  expect(result.manifest.environment).toMatchObject(ideaEnvironment);
  expect(result.artifacts.trades.length).toBeGreaterThan(0);
};

test(
  "그래프만으로 12-1 모멘텀을 만들어 정본 hash로 백테스트한다",
  { tag: ["@story", "@US-DM-07"] },
  async ({ page }) => {
    const hashes = await start(page, "12-1 모멘텀");
    await momentum(page, null, "12-1 모멘텀", 252, 21);
    // 일반 제작은 식별자 영역을 한 번도 열지 않고 검증 통과한다. 이후 정본 ID 재현은 선택 기능이다.
    await expect(await waitForSettledDocument(page)).toContainText("검증 통과");
    await expect(
      pipeline(page).getByRole("button", { name: "식별자(YAML)", exact: true }),
    ).toHaveAttribute("aria-expanded", "false");
    const card = pipeline(page).getByRole("group", {
      name: "12-1 모멘텀",
      exact: true,
    });
    await card
      .getByRole("button", { name: "식별자(YAML)", exact: true })
      .click();
    await commit(
      page,
      card.getByRole("textbox", { name: "팩터 이름", exact: true }),
      "momentum_12_1",
    );
    await card
      .getByRole("button", { name: "식별자(YAML)", exact: true })
      .click();
    await finish(page, "momentum_12_1", hashes);
  },
);

test(
  "그래프만으로 저PBR·고ROE와 자본총계 필터를 만들어 백테스트한다",
  { tag: ["@story", "@US-DM-07"] },
  async ({ page }) => {
    const hashes = await start(page, "저PBR + 고ROE");
    await eligibility(page, "financial.book_equity", "gt", "0");
    await factor(page, "pbr", "PBR", "low");
    await field(page, "price.market_cap");
    await binary(page, "나누기", "financial.book_equity");
    await closeRecipe(page);
    await factor(page, "roe", "ROE");
    await field(page, "financial.net_income");
    await binary(page, "나누기", "financial.book_equity");
    await closeRecipe(page);
    await finish(page, "low_pbr_high_roe", hashes);
  },
);

test(
  "그래프만으로 이평 돌파와 선언형 보조 모멘텀을 만들어 백테스트한다",
  { tag: ["@story", "@US-DM-07"] },
  async ({ page }) => {
    const hashes = await start(page, "20일 이평 돌파");
    await factor(page, "ma20_breakout", "20일 이평 돌파");
    await field(page, "price.adj_close");
    await period(page, "기간 평균", 20);
    await binary(page, "초과", "price.adj_close", "right_node_id");
    await closeRecipe(page);
    await momentum(page, "momentum_60", "동점 비교 모멘텀", 60);
    await pipeline(page)
      .getByRole("combobox", { name: "동점 해소 팩터", exact: true })
      .selectOption("momentum_60");
    await waitForSettledDocument(page);
    for (const width of [1440, 640, 360]) {
      await page.setViewportSize({ width, height: 1000 });
      await pipeline(page)
        .getByRole("group", { name: "문서 속성", exact: true })
        .scrollIntoViewIfNeeded();
      await page.screenshot({
        path: test.info().outputPath(`recipe-ideas-${width}.png`),
      });
    }
    await finish(page, "ma20_breakout", hashes);
  },
);

test(
  "그래프만으로 거래대금 상위 20%와 모멘텀을 만들어 백테스트한다",
  { tag: ["@story", "@US-DM-07"] },
  async ({ page }) => {
    const hashes = await start(page, "거래대금 상위 20%");
    await eligibility(page, "price.trading_value", "top_percent", "0.2");
    await momentum(page, "momentum_60", "60일 모멘텀", 60);
    await finish(page, "top_trading_value", hashes);
  },
);

test(
  "그래프만으로 변동성 역가중을 만들고 참조 이름을 한 번에 되돌린다",
  { tag: ["@story", "@US-DM-07"] },
  async ({ page }) => {
    const hashes = await start(page, "변동성 역가중");
    await momentum(page, "momentum_12_1", "12-1 모멘텀", 252, 21);
    await factor(page, "volatility_60", "60일 변동성", "low");
    await field(page, "price.adj_close");
    await period(page, "기간 수익률", 2);
    await period(page, "기간 표준편차", 60);
    await closeRecipe(page);
    const canvas = pipeline(page);
    await canvas
      .getByRole("combobox", { name: "비중 산정", exact: true })
      .selectOption("risk");
    await waitForSettledDocument(page);
    await canvas
      .getByRole("combobox", { name: "위험 팩터", exact: true })
      .selectOption("volatility_60");
    await waitForSettledDocument(page);
    const tie = canvas.getByRole("combobox", {
      name: "동점 해소 팩터",
      exact: true,
    });
    await tie.selectOption("volatility_60");
    await waitForSettledDocument(page);
    const card = canvas.getByRole("group", {
      name: "60일 변동성",
      exact: true,
    });
    await card
      .getByRole("button", { name: "식별자(YAML)", exact: true })
      .click();
    await commit(
      page,
      card.getByRole("textbox", { name: "팩터 이름", exact: true }),
      "renamed_volatility",
    );
    await expect(tie).toHaveValue("renamed_volatility");
    await expect(
      canvas.getByRole("combobox", { name: "위험 팩터", exact: true }),
    ).toHaveValue("renamed_volatility");
    await page.getByRole("button", { name: "실행 취소", exact: true }).click();
    await waitForSettledDocument(page);
    await expect(
      card.getByRole("textbox", { name: "팩터 이름", exact: true }),
    ).toHaveValue("volatility_60");
    await expect(tie).toHaveValue("volatility_60");
    await expect(
      canvas.getByRole("combobox", { name: "위험 팩터", exact: true }),
    ).toHaveValue("volatility_60");
    await card
      .getByRole("button", { name: "식별자(YAML)", exact: true })
      .click();
    await canvas
      .getByRole("button", { name: "동점 해소 팩터 · 기본값으로", exact: true })
      .click();
    await waitForSettledDocument(page);
    await commit(
      page,
      canvas.getByRole("spinbutton", {
        name: "종목별 최대 목표 비중 한도",
        exact: true,
      }),
      "0.1",
    );
    await finish(page, "inverse_volatility", hashes);
  },
);
