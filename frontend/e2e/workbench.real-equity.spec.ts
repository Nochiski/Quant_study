/**
 * 실데이터(카엘 서버 equity 층 로컬 사본) 위에서 그래프를 편집한 전략을 저장하고 백테스트가 끝까지 도는지
 * 확인하는 브라우저 시나리오. mock 어댑터가 아닌 duckdb 어댑터로 backend 를 띄워야 하므로
 * `STRATEGY_WORKBENCH_EQUITY_ADAPTER=duckdb` + `STRATEGY_WORKBENCH_EQUITY_ROOT` 가 없으면 전부 skip 한다
 * (CI 는 mock 그대로). 로컬 사본은 `database/scripts/ledger_sync.ps1 sync` 로 만든다(`database/docs/LEDGER_SYNC.md`).
 *
 *   $env:STRATEGY_WORKBENCH_EQUITY_ADAPTER = "duckdb"
 *   $env:STRATEGY_WORKBENCH_EQUITY_ROOT = "$HOME\quant-ledger\data\equity"
 *   npm run test:e2e -- --project real-equity
 */
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  compileStrategyDocument,
  getBacktestResult,
  getBacktestStatus,
  getStrategyDocument,
} from "../src/shared/api/generated";
import { createClient } from "../src/shared/api/generated/client";

const BACKEND = "http://localhost:8000";
const apiClient = createClient({ baseUrl: BACKEND });
const ownDirectory = dirname(fileURLToPath(import.meta.url));
const GOLDEN = readFileSync(
  resolve(
    ownDirectory,
    "../../backend/tests/fixtures/strategy_documents/quality_momentum.yaml",
  ),
  "utf8",
).replace(/\r\n?/gu, "\n");

const REAL_EQUITY_ENABLED =
  process.env.STRATEGY_WORKBENCH_EQUITY_ADAPTER === "duckdb" &&
  (process.env.STRATEGY_WORKBENCH_EQUITY_ROOT ?? "") !== "";
const MOCK_SNAPSHOT_ID = "mock-equity-v0.2-20260903";
// 실데이터 백테스트 구간 — 252 세션 모멘텀이 2023년 이력을 쓰고, 6개월이면 월별 리밸런싱 6회.
const BACKTEST_START = "2024-01-02";
const BACKTEST_END = "2024-06-28";
const REWIRED_FIELD = "price.open";
// 실데이터 security_id 어휘는 `{ticker}:{span_seq}`. 실행 설정 기본값 "005930" 은 이 형식이 아니라
// duckdb 어댑터가 `malformed security_id` 로 거절한다(GAP-09) — 삼성전자 첫 상장 구간을 명시한다.
const BENCHMARK_SECURITY_ID = "005930:1";

const editor = (page: Page) =>
  page.getByRole("textbox", { name: "편집기", exact: true });
const save = (page: Page) =>
  page.getByRole("button", { name: "리비전 저장", exact: true });
const backtest = (page: Page) =>
  page.getByRole("button", { name: "백테스트", exact: true });

const expectPhase = async (page: Page, phase: string) => {
  await expect(page.getByRole("status", { name: "문서 상태" })).toContainText(
    phase,
  );
};

const requireData = <Value>(
  data: Value | undefined,
  operation: string,
): Value => {
  if (data === undefined) throw new Error(`${operation} returned no data`);
  return data;
};

const currentSource = async (page: Page) => {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: "http://localhost:5173",
  });
  await editor(page).click();
  await editor(page).press("Control+A");
  await editor(page).press("Control+C");
  return page.evaluate(() => navigator.clipboard.readText());
};

const strategyIdentity = (page: Page) => {
  const match = new URL(page.url()).pathname.match(
    /^\/research\/strategies\/([^/]+)\/revisions\/(\d+)$/u,
  );
  if (match === null)
    throw new Error(`Not on a strategy revision: ${page.url()}`);
  return { strategyId: match[1]!, revision: Number(match[2]) };
};

const saveAndWaitForRevision = async (page: Page, revision: number) => {
  await expect(save(page)).toBeEnabled();
  await save(page).click();
  await expect(page).toHaveURL(
    new RegExp(
      `/research/strategies/[^/]+/revisions/${revision}(?:\\?.*)?$`,
      "u",
    ),
  );
  await expectPhase(page, "저장됨");
};

const realDataSource = (title: string): string =>
  GOLDEN.replace("퀄리티 모멘텀", title)
    .replace('start: "2021-01-01"', `start: "${BACKTEST_START}"`)
    .replace('end: "2026-08-31"', `end: "${BACKTEST_END}"`);

test.describe("real equity data", () => {
  test.skip(
    !REAL_EQUITY_ENABLED,
    "STRATEGY_WORKBENCH_EQUITY_ADAPTER=duckdb 와 STRATEGY_WORKBENCH_EQUITY_ROOT 가 필요하다",
  );
  test.describe.configure({ mode: "serial" });
  test.setTimeout(900_000);

  test("edits the graph on real data, saves the revision and completes a backtest", async ({
    page,
  }) => {
    const title = `실데이터 그래프 편집 ${Date.now().toString(36)}`;
    const response = await page.goto("/research/strategies/new");
    expect(response?.ok()).toBe(true);
    await expect(editor(page)).toBeVisible();
    await editor(page).fill(realDataSource(title));
    await expectPhase(page, "검증 통과");
    await saveAndWaitForRevision(page, 1);
    const { strategyId } = strategyIdentity(page);

    // Graph 편집: field 노드를 추가해 실데이터 필드(price.open)를 고르고, mom_252 의 입력을 그 노드로 재배선한다.
    await page.getByRole("tab", { name: "Graph", exact: true }).click();
    const graphEditor = page.getByRole("region", { name: "그래프 편집" });
    await expect(graphEditor).toBeVisible();
    await expect(graphEditor.getByText("편집 가능")).toBeVisible();
    await graphEditor
      .getByRole("combobox", { name: "노드 종류" })
      .selectOption("field");
    await graphEditor.getByRole("button", { name: "노드 추가" }).click();
    await expect(
      graphEditor.getByRole("status").filter({ hasText: "반영됨" }),
    ).toContainText("field 반영됨");
    const selected = graphEditor.getByRole("group", { name: /선택한 노드/ });
    const fieldId = selected.getByRole("combobox", { name: /^field_id/ });
    // 필드 목록은 duckdb 어댑터 `list_fields()` 가 실데이터에서 실제로 서비스하는 것만 담는다.
    await expect(fieldId.locator(`option[value="${REWIRED_FIELD}"]`)).toHaveCount(1);
    await fieldId.selectOption(REWIRED_FIELD);
    await expect(
      graphEditor.getByRole("status").filter({ hasText: "반영됨" }),
    ).toContainText("field_id 반영됨");
    await graphEditor.getByRole("button", { name: "노드 편집: mom_252" }).click();
    await selected
      .getByRole("combobox", { name: /^input_node_id/ })
      .selectOption("field");
    await expect(
      graphEditor.getByRole("status").filter({ hasText: "반영됨" }),
    ).toContainText("input_node_id 반영됨");

    // 편집은 source 트랜잭션이다: YAML 에 그대로 들어 있고 compile 이 다시 통과한다.
    await page.getByRole("tab", { name: "YAML", exact: true }).click();
    await expectPhase(page, "검증 통과");
    const edited = await currentSource(page);
    expect(edited).toContain(`\n          field_id: ${REWIRED_FIELD}\n`);
    expect(edited).toContain("\n          input_node_id: field\n");
    expect(edited).toContain(`start: "${BACKTEST_START}"`);
    await saveAndWaitForRevision(page, 2);
    const saved = requireData(
      (
        await getStrategyDocument({
          client: apiClient,
          path: { strategy_id: strategyId, revision: 2 },
        })
      ).data,
      "graph-edited revision",
    );
    const compiled = requireData(
      (
        await compileStrategyDocument({
          client: apiClient,
          body: { source: edited, format: "yaml" },
        })
      ).data,
      "graph-edited compile",
    );
    expect(saved.spec_hash).toBe(compiled.spec_hash);
    expect(saved.spec.data.universe_id).toBe("krx.common-stock");

    // 백테스트: 실데이터 duckdb 어댑터 + Rust core. 저장된 revision 을 그대로 실행한다.
    const settingsToggle = page.getByLabel("실행 설정 열기");
    await settingsToggle.click();
    await expect(page.getByRole("combobox", { name: "실행 core" })).toHaveValue("rust");
    await page
      .getByRole("textbox", { name: "벤치마크 종목 ID" })
      .fill(BENCHMARK_SECURITY_ID);
    await expect(page.getByText("준비됨", { exact: true })).toBeVisible();
    await settingsToggle.click();
    await expect(backtest(page)).toBeEnabled();
    const submittedRun = page.waitForRequest(
      (request) =>
        request.method() === "POST" &&
        new URL(request.url()).pathname === "/api/v1/backtests",
    );
    // POST /api/v1/backtests 는 run id 를 돌려주기 전에 TargetTape(전 유니버스 팩터 평가)를 동기로
    // 만든다. mock 은 순간이지만 실데이터(공통주 ~2천 종목 × 6개월 + 252 세션 이력)는 수십 초가 걸린다.
    const acceptedRun = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/api/v1/backtests",
      { timeout: 600_000 },
    );
    await backtest(page).click();
    expect((await submittedRun).postDataJSON()).toMatchObject({
      core: "rust",
      benchmark_security_id: BENCHMARK_SECURITY_ID,
      strategy_source: {
        kind: "saved_revision",
        strategy_id: strategyId,
        revision: 2,
        expected_spec_hash: saved.spec_hash,
      },
    });
    expect((await acceptedRun).status()).toBe(202);
    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u, {
      timeout: 60_000,
    });
    const runId = new URL(page.url()).pathname.split("/").at(-1)!;
    // 실패하면 화면의 "오류 failed" 만으로는 원인을 알 수 없다 — 서버 run 상태의 error 를 단언 메시지에 싣는다.
    const finalState = await expect
      .poll(
        async () => {
          const state = requireData(
            (await getBacktestStatus({ client: apiClient, path: { run_id: runId } })).data,
            "poll backtest run state",
          );
          return state;
        },
        { timeout: 600_000, intervals: [2_000] },
      )
      .toMatchObject({ status: expect.stringMatching(/^(completed|failed|cancelled)$/u) })
      .then(async () =>
        requireData(
          (await getBacktestStatus({ client: apiClient, path: { run_id: runId } })).data,
          "final backtest run state",
        ),
      );
    expect(finalState.status, JSON.stringify(finalState.error)).toBe("completed");
    await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
      "completed",
      { timeout: 60_000 },
    );
    await expect(
      page.getByRole("article", { name: "백테스트 결과" }),
    ).toBeVisible({ timeout: 60_000 });

    const result = requireData(
      (
        await getBacktestResult({
          client: apiClient,
          path: { run_id: runId },
        })
      ).data,
      "get completed backtest result",
    );
    expect(result.manifest).toMatchObject({
      run_id: runId,
      strategy_hash: saved.spec_hash,
      strategy_provenance: {
        kind: "saved_revision",
        strategy_id: strategyId,
        revision: 2,
        spec_hash: saved.spec_hash,
      },
    });
    // 실데이터 스냅샷 id = equity 루트의 전 테이블 build_id 정렬 해시(16 hex). mock id 가 아니다.
    expect(result.manifest.data_snapshot_id).not.toBe(MOCK_SNAPSHOT_ID);
    expect(result.manifest.data_snapshot_id).toMatch(/^[0-9a-f]{16}$/u);
    expect(result.manifest.run_spec.strategy?.title).toBe(title);
    // 실제로 거래가 일어났다: 체결·스냅샷·자본 곡선이 비어 있지 않다.
    expect(result.artifacts.fills.length).toBeGreaterThan(0);
    expect(result.artifacts.snapshots.length).toBeGreaterThan(0);
    expect(result.series.equity.length).toBeGreaterThan(0);
    const totalReturn = result.metrics.find(
      (metric) => metric.metric_id === "total_return" && metric.scope === "full",
    );
    expect(totalReturn?.value).not.toBeNull();
  });
});
