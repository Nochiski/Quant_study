/**
 * 실데이터(카엘 서버 equity 층 로컬 사본) 위에서 그래프를 편집한 전략을 저장하고 백테스트가 끝까지 도는지
 * 확인하는 브라우저 시나리오. duckdb 어댑터로 backend 를 띄워야 하므로 opt-in 변수
 * `E2E_REAL_EQUITY_ROOT`(로컬 equity 루트) 가 없으면 전부 skip 한다. `playwright.config.ts` 는 그 변수가
 * 있을 때만 이 project 를 수집하고 backend 에 duckdb 어댑터 환경변수를 넘긴다 — mock 전제의 릴리스 게이트
 * 프로젝트와 한 실행에 섞이지 않는다. 로컬 사본은 `database/scripts/ledger_sync.ps1 sync` 로 만든다
 * (`database/docs/LEDGER_SYNC.md`). 사본은 최소 2023-01 이후 세션을 덮어야 252 세션 모멘텀이 성립한다.
 *
 *   $env:E2E_REAL_EQUITY_ROOT = "$HOME\quant-ledger\data\equity"
 *   npm run test:e2e
 */
import { expect, test } from "@playwright/test";

import {
  compileStrategyDocument,
  getBacktestResult,
  getBacktestStatus,
  getStrategyDocument,
  type BacktestRunState,
} from "../src/shared/api/generated";
import {
  apiClient,
  backtest,
  currentSource,
  expectPhase,
  GOLDEN,
  mustReplace,
  openEditor,
  replaceSource,
  requireData,
  saveAndWaitForRevision,
  strategyIdentity,
} from "./workbench-helpers";

export const REAL_EQUITY_ROOT_ENV = "E2E_REAL_EQUITY_ROOT";
const REAL_EQUITY_ENABLED = (process.env[REAL_EQUITY_ROOT_ENV] ?? "") !== "";
// 실데이터 백테스트 구간 — 252 세션 모멘텀이 2023년 이력을 쓰고, 6개월이면 월별 리밸런싱 6회.
const BACKTEST_START = "2024-01-02";
const BACKTEST_END = "2024-06-28";
const REWIRED_FIELD = "price.open";
// 실데이터 security_id 어휘는 `{ticker}:{span_seq}`(GAP-11). 실행 설정 기본값은 비어 있어(벤치마크 없음,
// 이슈 #154) 벤치마크 경로까지 검증하려면 어댑터 어휘의 ID 를 명시해야 한다 — 삼성전자 첫 상장 구간.
const BENCHMARK_SECURITY_ID = "005930:1";
// 시작 요청은 데이터를 읽지 않는 사전 검사만 하고 즉시 202 를 돌려준다(이슈 #158). 시작 예산은
// 밀리초 단위 실측보다 훨씬 크지만 데이터 크기에 비례할 수 없는 값으로 조여 두어, 누군가 시작 경로에
// 데이터 읽기를 되돌려 넣으면 여기서 잡힌다. TargetTape 계산은 run 의 `tape` 단계로 옮겨졌다 —
// 실측(2026-09-19, 6개월 구간) run 전체(tape + 엔진) 약 30초(상태 폴링 119회 × 250ms), 테스트 본문
// 1.1분. 완료 예산은 그 10배 이상이다. 실행 구간 하위 예산 합(15+60+30+420+60+60 = 645s)이 테스트
// 예산(900s) 안에 들어와야 하위 단계가 먼저 실패해 원인을 말한다.
const START_TIMEOUT_MS = 15_000;
const COMPLETE_TIMEOUT_MS = 420_000;
const TEST_TIMEOUT_MS = 900_000;

const realDataSource = (title: string): string => {
  const titled = mustReplace(GOLDEN, "퀄리티 모멘텀", title);
  const started = mustReplace(titled, 'start: "2021-01-01"', `start: "${BACKTEST_START}"`);
  return mustReplace(started, 'end: "2026-08-31"', `end: "${BACKTEST_END}"`);
};

test.describe("real equity data", () => {
  test.skip(
    !REAL_EQUITY_ENABLED,
    `${REAL_EQUITY_ROOT_ENV} 가 없다 — 실데이터 project 는 opt-in 이다`,
  );
  test.describe.configure({ mode: "serial" });
  test.setTimeout(TEST_TIMEOUT_MS);

  // P3-02(실행 설정 패널)에서 되살린다. schema 1.2 는 실행 기간·유니버스를 전략 문서에서 빼
  // 실행 요청의 `environment` 로 옮겼고(P2-03), 그 값을 싣는 프론트 배선이 그 패널이다. 기간에는
  // 스키마 기본값이 있을 수 없어 요청에 상수를 박는 shim 으로 앞당길 수 없다. 그때까지 브라우저에서
  // 시작한 백테스트는 422 `backtest.run.environment_required` 로 거절된다. 최종 시나리오 재작성은
  // P3-03(e2e fixture 1.2)이 맡는다.
  // 되살릴 때 바꿀 것: `realDataSource` 가 GOLDEN 의 `start`/`end` 를 치환하는데 1.2 문서에는 그
  // 키가 없어 `mustReplace` 가 던진다 — 구간은 실행 설정으로 옮겨야 한다. 그리고 기대 요청 본문의
  // `environment`.
  test.fixme("edits the graph on real data, saves the revision and completes a backtest", async ({
    page,
  }) => {
    const title = `실데이터 그래프 편집 ${Date.now().toString(36)}`;
    await openEditor(page, "/research/strategies/new");
    await replaceSource(page, realDataSource(title));
    await expectPhase(page, "검증 통과");
    await saveAndWaitForRevision(page, 1);
    const { strategyId } = strategyIdentity(page);

    // Graph 편집: field 노드를 추가해 실데이터 필드(price.open)를 고르고, mom_252 의 입력을 그 노드로 재배선한다.
    await page.getByRole("tab", { name: "Graph", exact: true }).click();
    const graphEditor = page.getByRole("region", { name: "그래프 편집" });
    await expect(graphEditor).toBeVisible();
    await expect(graphEditor.getByText("편집 가능")).toBeVisible();
    // 트랜잭션이 거부되면 role=alert 로 사유가 뜬다 — status 만 기다리다 timeout 으로 끝나지 않게 먼저 본다.
    const expectApplied = async (label: string) => {
      await expect(graphEditor.getByRole("alert")).toHaveCount(0);
      await expect(
        graphEditor.getByRole("status").filter({ hasText: "반영됨" }),
      ).toContainText(`${label} 반영됨`);
    };
    await graphEditor
      .getByRole("combobox", { name: "노드 종류" })
      .selectOption("field");
    await graphEditor.getByRole("button", { name: "노드 추가" }).click();
    await expectApplied("field");
    const selected = graphEditor.getByRole("group", { name: /선택한 노드/ });
    const fieldId = selected.getByRole("combobox", { name: /^field_id/ });
    // 필드 목록은 duckdb 어댑터 `list_fields()` 가 실데이터에서 실제로 서비스하는 것만 담는다.
    await fieldId.selectOption(REWIRED_FIELD);
    await expect(fieldId).toHaveValue(REWIRED_FIELD);
    await expectApplied("field_id");
    await graphEditor.getByRole("button", { name: "노드 편집: mom_252" }).click();
    await selected
      .getByRole("combobox", { name: /^input_node_id/ })
      .selectOption("field");
    await expectApplied("input_node_id");

    // 편집은 source 트랜잭션이다: YAML 에 그대로 들어 있고 compile 이 다시 통과한다.
    await page.getByRole("tab", { name: "YAML", exact: true }).click();
    await expectPhase(page, "검증 통과");
    const edited = await currentSource(page);
    expect(edited).toContain(`\n          field_id: ${REWIRED_FIELD}\n`);
    expect(edited).toContain("\n          input_node_id: field\n");
    expect(edited).toContain(`start: "${BACKTEST_START}"`);
    expect(edited).toContain(`end: "${BACKTEST_END}"`);
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
    // 유니버스는 schema 1.2 에서 전략 문서를 떠나 실행 설정이 소유한다(P2-03). 저장된 spec 으로
    // 확인할 수 있는 것은 문서에 남은 쪽이다.
    expect((saved.spec.factors ?? []).map((factor) => factor.factor_id)).toEqual([
      "momentum",
    ]);

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
    // POST /api/v1/backtests 는 사전 검사만 하고 바로 run id 를 돌려준다. TargetTape(전 유니버스 팩터
    // 평가)는 run 의 tape 단계에서 만들어지며 실데이터(공통주 ~2천 종목 × 6개월 + 252 세션 이력)는
    // 수십 초가 걸린다 — 아래 완료 폴링이 그 시간을 흡수한다.
    const acceptedRun = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/api/v1/backtests",
      { timeout: START_TIMEOUT_MS },
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
    // tape 단계는 실데이터에서 수십 초 이상 이어지므로 run 페이지가 그 단계를 실제로 보여 주는지 본다.
    await expect(page.getByRole("status", { name: "실행 진행" })).toContainText(
      "tape",
      { timeout: 30_000 },
    );
    // 실패하면 화면의 "오류 failed" 만으로는 원인을 알 수 없다 — 서버 run 상태의 error 를 단언 메시지에 싣는다.
    let finalState: BacktestRunState | undefined;
    await expect
      .poll(
        async () => {
          finalState = requireData(
            (await getBacktestStatus({ client: apiClient, path: { run_id: runId } })).data,
            "poll backtest run state",
          );
          return finalState.status;
        },
        { timeout: COMPLETE_TIMEOUT_MS, intervals: [2_000] },
      )
      .toMatch(/^(completed|failed|cancelled)$/u);
    const terminal = requireData(finalState, "terminal backtest run state");
    expect(terminal.status, JSON.stringify(terminal.error)).toBe("completed");
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
    // 실데이터 스냅샷 id = equity 루트의 전 테이블 build_id 정렬 sha256 앞 16자리. mock id 는 hex 가
    // 아니므로 이 정규식이 mock 어댑터를 배제한다.
    expect(result.manifest.data_snapshot_id).toMatch(/^[0-9a-f]{16}$/u);
    expect(result.manifest.run_spec.strategy?.title).toBe(title);
    // 실제로 거래가 일어났다: 체결·스냅샷·자본 곡선이 비어 있지 않다.
    expect(result.artifacts.fills.length).toBeGreaterThan(0);
    expect(result.artifacts.snapshots.length).toBeGreaterThan(0);
    expect(result.series.equity.length).toBeGreaterThan(0);
    const totalReturn = requireData(
      result.metrics.find(
        (metric) => metric.metric_id === "total_return" && metric.scope === "full",
      ),
      "total_return (full) metric",
    );
    expect(totalReturn.value).not.toBeNull();
  });
});
