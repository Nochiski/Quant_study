/**
 * 한상목의 스토리 e2e — 실험을 표로 만들고 다시 만들며(US-SM-14), 여러 실험을 대기열에 걸어 두고(US-SM-15),
 * 같은 계산이 겹쳐 돌지 않는다(US-SM-11).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/sm.md`다. 조합·창·실행 수, 실험·trial
 * 상태, 슬롯 사용량, 공유 실행의 소유자는 backend 가 정한다(검증 랩 spec D5·D6). mock 실행은 1초 안에
 * 끝나므로 backend 의 trial 붙잡기 훅(`TRIAL_HOLD_SECONDS`)으로 실험 run 을 붙잡아 대기·일시정지·취소를
 * 화면에서 본다. 사용자 단일 실행은 붙잡히지 않는다.
 */
import { expect, test, type Page } from "@playwright/test";

import {
  createExperiment,
  createStrategyDocument,
  getBacktestRequest,
  getBacktestStatus,
  listExperiments,
  listExperimentTrials,
  startBacktest,
  type BacktestRunSpec,
  type ContinuousAccountState,
  type StrategyDocument,
} from "../../src/shared/api/generated";
import { TRIAL_HOLD_SECONDS } from "../runtime";
import {
  apiClient,
  GOLDEN,
  mustReplace,
  REQUESTED_ENVIRONMENT,
  requireData,
} from "../workbench-helpers";

/** 값 두 개(0, 1)를 펴는 파라미터 하나. 엔진은 아직 파라미터를 읽지 않지만 칸마다 run 이 따로 돈다. */
const WITH_PARAMETER = mustReplace(
  GOLDEN,
  "parameters: []",
  [
    "parameters:",
    "  - parameter_id: scale",
    "    kind: float",
    "    default: 0.0",
    "    minimum: 0.0",
    "    maximum: 1.0",
    "    step: 1.0",
  ].join("\n"),
);

/** 학습 1년·검증 1년 롤링이면 창이 하나인 기간 — 실험 하나가 run 세 개(칸 둘 + 검증 하나)로 끝난다. */
const ENVIRONMENT = {
  ...REQUESTED_ENVIRONMENT,
  start: "2021-01-04",
  end: "2022-12-30",
};

const saveStrategy = async (title: string): Promise<StrategyDocument> =>
  requireData(
    (
      await createStrategyDocument({
        client: apiClient,
        body: {
          format: "yaml",
          source: mustReplace(WITH_PARAMETER, "퀄리티 모멘텀", title),
        },
      })
    ).data,
    "create strategy document",
  );

const baseRequest = (saved: StrategyDocument): BacktestRunSpec => ({
  strategy_source: {
    kind: "saved_revision",
    strategy_id: saved.strategy_id,
    revision: saved.revision,
    expected_spec_hash: saved.spec_hash,
  },
  environment: ENVIRONMENT,
});

const runStatus = async (runId: string) =>
  requireData(
    (await getBacktestStatus({ client: apiClient, path: { run_id: runId } }))
      .data,
    "poll backtest run state",
  ).status;

const startRun = async (spec: BacktestRunSpec): Promise<string> =>
  requireData(
    (await startBacktest({ client: apiClient, body: spec })).data,
    "start backtest",
  ).run.run_id;

/** 이 전략으로 만든 실험들의 id — 만든 순서대로. `count` 개가 생길 때까지 기다린다. */
const listedIds = async (saved: StrategyDocument): Promise<string[]> =>
  requireData(
    (await listExperiments({ client: apiClient })).data,
    "list experiments",
  )
    .items.filter(
      (item) =>
        item.record.run.strategy_source?.kind === "saved_revision" &&
        item.record.run.strategy_source.strategy_id === saved.strategy_id,
    )
    .sort((a, b) => a.record.created_at.localeCompare(b.record.created_at))
    .map((item) => item.record.experiment_id);

const experimentIds = async (
  saved: StrategyDocument,
  count: number,
): Promise<string[]> => {
  await expect.poll(async () => (await listedIds(saved)).length).toBe(count);
  return listedIds(saved);
};

const experimentRow = (page: Page, experimentId: string) =>
  page
    .getByRole("row")
    .filter({ has: page.getByRole("link", { name: experimentId }) });

/** 실험 행의 상태 칸(배지). 진행 수 칸에도 "실행 중"이 있어 행 전체로 단언하면 늘 참이다. */
const experimentStatus = (page: Page, experimentId: string) =>
  experimentRow(page, experimentId).getByRole("cell").nth(2);

test(
  "US-SM-14 US-SM-15 실험을 표로 보고 같은 설정으로 다시 만들며, 대기열의 두 실험을 조작하고 완료 알림으로 후보를 연다",
  { tag: ["@story", "@US-SM-14", "@US-SM-15"] },
  async ({ context }, testInfo) => {
    test.setTimeout(300_000);
    const saved = await saveStrategy(`US-SM-15 실험 대기열 ${Date.now()}`);
    const baseRun = await startRun(baseRequest(saved));
    await expect
      .poll(() => runStatus(baseRun), { timeout: 60_000 })
      .toBe("completed");

    // US-SM-14: 백테스트 결과에서 새 실험 화면을 열고, 탐색 공간·분할을 고르면 실행 전에 수를 본다.
    let page = await context.newPage();
    await page.goto(`/research/backtests/${baseRun}`);
    await page.getByRole("link", { name: "이 실행으로 실험 만들기" }).click();
    await expect(page.getByRole("heading", { name: "새 실험" })).toBeVisible();
    await page.getByRole("checkbox", { name: "탐색: scale" }).check();
    await page.getByLabel("학습(년)").fill("1");
    await page.getByLabel("검증(년)").fill("1");
    await page.getByLabel("엠바고(세션)").fill("0");
    await expect(
      page.getByRole("cell", { name: "0, 1 (2)", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(
        /조합 2개 · 창 1개 · 백테스트 실행 \d+회 · 계열 시도 수 \d+회 → \d+회/u,
      ),
    ).toBeVisible();
    // US-SM-14: 새 계열이라 근거는 끝난 기반 실행 하나다.
    await expect(
      page.getByText(/^예상 소요 최대 약 .+ · 이 계열의 최근 완료 실행 1개를/u),
    ).toBeVisible();
    // US-SM-14: 탐색 값을 직접 적거나 조합 제약을 걸면 조합 수가 줄고, 지우면 정의의 격자로 돌아간다.
    const values = page.getByLabel("값 직접 입력: scale");
    await values.fill("1");
    await expect(page.getByText(/조합 1개 · 창 1개/u)).toBeVisible();
    await values.fill("");
    await expect(page.getByText(/조합 2개 · 창 1개/u)).toBeVisible();
    await page.getByRole("button", { name: "제약 추가" }).click();
    await page
      .getByRole("combobox", { name: "조합 제약 1: 비교" })
      .selectOption("ge");
    await page.getByLabel("조합 제약 1: 값").fill("1");
    await expect(page.getByText(/조합 1개 · 창 1개/u)).toBeVisible();
    await page.getByRole("button", { name: "제약 삭제" }).click();
    await expect(page.getByText(/조합 2개 · 창 1개/u)).toBeVisible();
    await page.getByRole("button", { name: "대기열에 넣기" }).click();
    const [first = ""] = await experimentIds(saved, 1);
    await expect(
      page.getByRole("heading", { name: "실험", exact: true }),
    ).toBeVisible();

    // US-SM-14: 같은 설정으로 새 실험을 열면 탐색 공간과 분할이 그대로 채워진다. 엠바고만 바꿔 다른 실험을 건다
    // (같은 요청이면 두 실험이 같은 run 을 나눠 쓴다).
    await experimentRow(page, first)
      .getByRole("link", { name: "같은 설정으로 새 실험" })
      .click();
    await expect(
      page.getByRole("checkbox", { name: "탐색: scale" }),
    ).toBeChecked();
    await expect(page.getByLabel("학습(년)")).toHaveValue("1");
    await expect(page.getByLabel("검증(년)")).toHaveValue("1");
    await expect(page.getByLabel("엠바고(세션)")).toHaveValue("0");
    await page.getByLabel("엠바고(세션)").fill("1");
    await expect(page.getByText(/조합 2개 · 창 1개/u)).toBeVisible();
    await page.getByRole("button", { name: "대기열에 넣기" }).click();
    const [, second = ""] = await experimentIds(saved, 2);

    // US-SM-15: 앞 실험이 실험 몫 슬롯을 붙잡고 있는 동안 두 실험이 실행 중과 대기로 보이고 슬롯 사용량이 보인다.
    await expect(experimentStatus(page, first)).toContainText("실행 중");
    await expect(experimentStatus(page, second)).toContainText("대기");
    await expect(
      page.getByText(/동시 실행 슬롯 \d+ \/ \d+ 사용 중/u),
    ).toBeVisible();

    // 일시정지했다 재개하고, 뒤 실험은 취소한다(앞 실험만 끝까지 돈다).
    await experimentRow(page, second)
      .getByRole("button", { name: "일시정지" })
      .click();
    await expect(experimentStatus(page, second)).toContainText("일시정지");
    await experimentRow(page, second)
      .getByRole("button", { name: "재개" })
      .click();
    await expect(
      experimentRow(page, second).getByRole("button", { name: "일시정지" }),
    ).toBeVisible();
    await experimentRow(page, second)
      .getByRole("button", { name: "취소" })
      .click();
    await experimentRow(page, second)
      .getByRole("button", { name: "실험 취소 확인" })
      .click();
    await expect(experimentStatus(page, second)).toContainText("취소됨");

    // 실험이 슬롯을 쓰는 동안에도 단일 백테스트는 기다리지 않고 끝난다.
    await expect(experimentStatus(page, first)).toContainText("실행 중");
    const single = await startRun(baseRequest(saved));
    await expect
      .poll(() => runStatus(single), { timeout: TRIAL_HOLD_SECONDS * 500 })
      .toBe("completed");

    // 창을 닫았다 다시 열어도 실험은 서버에서 계속 돈다. 다른 화면에 있어도 끝나면 알림이 뜬다.
    await page.close();
    page = await context.newPage();
    await page.goto("/research/experiments");
    await expect(experimentStatus(page, first)).toContainText("실행 중");
    // 도는 실험의 모니터는 남은 시간 어림을 보인다(계열에 끝난 기반·단일 실행이 근거다).
    await page.goto(`/research/experiments/${first}`);
    await expect(page.getByText(/^남은 시간 최대 약 /u)).toBeVisible();
    await page.goto("/research/backtests");
    const notice = page
      .getByRole("status")
      .filter({ hasText: `실험 ${first} 완료` });
    await expect(notice).toBeVisible({ timeout: TRIAL_HOLD_SECONDS * 8_000 });
    await expect(
      page.getByRole("status").filter({ hasText: `실험 ${second} 완료` }),
    ).toHaveCount(0);
    // 완료된 실험의 보조 조회만 실패시켜, 결과 없음과 조회 실패를 구분하고 원본 조회로 복구한다.
    const walkForwardPath = `**/api/v1/experiments/${first}/walk-forward`;
    const auditPath = `**/api/v1/experiments/${first}/audit`;
    for (const path of [walkForwardPath, auditPath])
      await page.route(path, (route) =>
        route.fulfill({ status: 503, json: { detail: "temporary failure" } }),
      );
    await notice.getByRole("link", { name: "후보 보기" }).click();
    await expect(
      page.getByRole("heading", { name: `실험 ${first}` }),
    ).toBeVisible();
    const trials = page.getByRole("table", { name: "trial 목록" });
    await expect(trials.getByRole("row")).toHaveCount(3);
    await expect(trials.getByText("완료")).toHaveCount(2);
    await expect(
      page.getByRole("alert").filter({
        hasText: "워크포워드 결과를 불러오지 못했습니다.",
      }),
    ).toBeVisible();
    await expect(
      page.getByRole("alert").filter({
        hasText: "감사 기록을 불러오지 못했습니다.",
      }),
    ).toBeVisible();
    await page.unroute(walkForwardPath);
    await page
      .getByRole("button", { name: "다시 불러오기", exact: true })
      .click();
    await page.unroute(auditPath);
    await page
      .getByRole("button", { name: "감사 기록 다시 불러오기", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "워크포워드 결과" }),
    ).toBeVisible();
    await expect(
      page.getByText(/같은 초기자본으로 다시 실행.*첫 snapshot 이후/u),
    ).toBeVisible();
    await expect(
      page.getByText(
        /현금·보유 수량을 이월하지.*실제 연속 계좌의 손익이 아닙니다/u,
      ),
    ).toBeVisible();
    // 좁은 화면에서도 검증 창 표에 초점을 두고 방향키로 마지막 열까지 읽을 수 있다.
    const originalViewport = page.viewportSize();
    await page.setViewportSize({ width: 360, height: 800 });
    const windows = page.getByRole("region", { name: "워크포워드 검증 창" });
    await windows.focus();
    await windows.press("ArrowRight");
    await expect
      .poll(() => windows.evaluate((element) => element.scrollLeft))
      .toBeGreaterThan(0);
    const auditRegion = page.getByRole("region", {
      name: "감사 기록",
      exact: true,
    });
    await auditRegion.focus();
    await auditRegion.press("ArrowRight");
    await expect
      .poll(() => auditRegion.evaluate((element) => element.scrollLeft))
      .toBeGreaterThan(0);
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(360);
    if (originalViewport) await page.setViewportSize(originalViewport);
    // 감사 기록: 생성부터 실행 시작·끝까지 시각 순으로 남는다.
    const audit = page.getByRole("table", { name: "감사 기록" });
    await expect(audit.getByText("실험 생성")).toBeVisible();
    await expect(audit.getByText("실행 시작").first()).toBeVisible();
    await expect(audit.getByText("실행 끝").first()).toBeVisible();

    // 연속 평가는 실제 mock 서버의 별도 실행이며 창별 재시작 결과를 유지한다.
    const continuous = page.getByRole("region", {
      name: "연속 계좌 평가",
      exact: true,
    });
    await expect(
      continuous.getByText(/학습 종료일 종가.*과거 실제 선택의 증거/u),
    ).toBeVisible();
    const startContinuous = continuous.getByRole("button", {
      name: "연속 계좌 평가 실행",
      exact: true,
    });
    await expect(startContinuous).toBeEnabled({ timeout: 60_000 });
    const evaluated = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/experiments/${first}/continuous-account`),
    );
    await startContinuous.click();
    const evaluatedResponse = await evaluated;
    expect(evaluatedResponse.ok()).toBe(true);
    const evaluatedState =
      (await evaluatedResponse.json()) as ContinuousAccountState;
    expect(evaluatedState.status).toBe("completed");
    await expect(
      continuous.getByText("계좌 평가 완료", { exact: true }),
    ).toBeVisible();
    await expect(
      continuous.getByText("초기자본 대비 평가손익", { exact: true }),
    ).toBeVisible();
    const recorded = evaluatedState.result!.summary;
    await expect(
      continuous.getByText(
        `계좌 기록 기간 ${recorded.first_snapshot_date} ~ ${recorded.last_snapshot_date} · ${recorded.snapshot_count}개 기록`,
      ),
    ).toBeVisible();
    await expect(
      continuous.getByText("모의 자료", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(
        /현금·보유 수량을 이월하지.*실제 연속 계좌의 손익이 아닙니다/u,
      ),
    ).toBeVisible();
    await continuous.scrollIntoViewIfNeeded();
    await testInfo.attach("continuous-account-desktop", {
      body: await continuous.screenshot(),
      contentType: "image/png",
    });
    await page.screenshot({
      path: testInfo.outputPath("experiment-desktop.png"),
      fullPage: true,
    });
    await page.reload();
    await expect(
      continuous.getByText("계좌 평가 완료", { exact: true }),
    ).toBeVisible();
    await expect(
      continuous.getByRole("button", { name: "다시 평가", exact: true }),
    ).toBeEnabled();
    await page.setViewportSize({ width: 360, height: 800 });
    const allMetrics = continuous.getByText("위험·성과 지표 전체 보기", {
      exact: true,
    });
    await allMetrics.focus();
    await allMetrics.press("Enter");
    const metricTable = continuous.getByRole("table", {
      name: "Raw metric table",
      exact: true,
    });
    for (const metricId of ["cagr", "sharpe", "max_drawdown"])
      await expect(
        metricTable.getByRole("cell", { name: metricId, exact: true }),
      ).toBeVisible();
    const metricRegion = continuous.getByRole("region", {
      name: "Raw metric table",
      exact: true,
    });
    await metricRegion.focus();
    await metricRegion.press("ArrowRight");
    await expect
      .poll(() => metricRegion.evaluate((element) => element.scrollLeft))
      .toBeGreaterThan(0);
    await allMetrics.focus();
    await allMetrics.press("Enter");
    const fillSummary = continuous.getByText(/^체결 내역 \d+건$/u);
    await fillSummary.focus();
    await fillSummary.press("Enter");
    const fillRegion = continuous.getByRole("region", {
      name: "연속 계좌 체결 내역",
      exact: true,
    });
    await expect(fillRegion.getByRole("row")).toHaveCount(
      evaluatedState.result!.artifacts.fills.length + 1,
    );
    await fillRegion.focus();
    await fillRegion.press("ArrowRight");
    await expect
      .poll(() => fillRegion.evaluate((element) => element.scrollLeft))
      .toBeGreaterThan(0);
    await fillSummary.focus();
    await fillSummary.press("Enter");
    const provenance = continuous.getByText("평가 출처와 주의사항", {
      exact: true,
    });
    await provenance.focus();
    await provenance.press("Enter");
    await expect(
      continuous.getByText("assumed_train_end_close", { exact: true }),
    ).toBeVisible();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(360);
    await provenance.press("Enter");
    await continuous.scrollIntoViewIfNeeded();
    await testInfo.attach("continuous-account-mobile", {
      body: await continuous.screenshot(),
      contentType: "image/png",
    });
    if (originalViewport) await page.setViewportSize(originalViewport);

    // 조회 실패는 기존 완료 결과를 지우지 않으며 조회만 다시 한다.
    const continuousLatestPath = `**/api/v1/experiments/${first}/continuous-account`;
    await page.route(continuousLatestPath, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({
            status: 503,
            json: { detail: "temporary lookup failure" },
          })
        : route.continue(),
    );
    await page.reload();
    await expect(
      continuous.getByText(
        "평가 상태를 불러오지 못했습니다. 실행 여부를 확인하려면 상태를 다시 불러오세요.",
      ),
    ).toBeVisible();
    await expect(
      continuous.getByText("계좌 평가 완료", { exact: true }),
    ).toBeVisible();
    await page.unroute(continuousLatestPath);
    await continuous
      .getByRole("button", { name: "연속 계좌 평가 다시 불러오기" })
      .click();
    await expect(continuous.getByRole("alert")).toHaveCount(0);

    const continuousDetailPath = `**/api/v1/continuous-accounts/${evaluatedState.evaluation_id}`;
    await page.route(continuousDetailPath, (route) =>
      route.fulfill({
        status: 410,
        json: { detail: "permanent lookup failure" },
      }),
    );
    await page.reload();
    await expect(
      continuous.getByText(
        "이 평가를 다시 조회할 수 없습니다. 진단 사유를 확인하세요.",
      ),
    ).toBeVisible();
    await expect(
      continuous.getByRole("button", { name: /다시 불러오기|평가 상태 확인/u }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "워크포워드 결과", exact: true }),
    ).toBeVisible();
    await page.unroute(continuousDetailPath);
    // 실행 실패의 표시 검증만 응답을 치환하며 실제 완료된 원본은 변경하지 않는다.
    await page.route(continuousDetailPath, (route) =>
      route.fulfill({
        json: {
          ...evaluatedState,
          status: "failed",
          result: null,
          error_code: "continuous.execution_failed",
          error_detail: "합성 실행 실패",
        },
      }),
    );
    await page.reload();
    await expect(
      continuous.getByText(
        "계좌 평가 실행이 실패했습니다. 아래 사유를 확인하세요. 기존 실험 결과는 계속 볼 수 있습니다.",
      ),
    ).toBeVisible();
    await expect(
      continuous.getByText(
        "이 평가를 다시 조회할 수 없습니다. 진단 사유를 확인하세요.",
      ),
    ).toHaveCount(0);
    await expect(
      page.getByRole("table", { name: "워크포워드 결과", exact: true }),
    ).toBeVisible();
  },
);

test(
  "US-SM-11 실험이 도는 실행을 같은 요청으로 다시 시작하면 같은 실행으로 가고, 내 취소는 내 몫만 빼 실험을 취소해야 멈춘다",
  { tag: ["@story", "@US-SM-11"] },
  async ({ page }) => {
    test.setTimeout(120_000);
    const saved = await saveStrategy(`US-SM-11 공유 실행 ${Date.now()}`);
    const { experiment_id: experimentId } = requireData(
      (
        await createExperiment({
          client: apiClient,
          body: {
            run: baseRequest(saved),
            search: { scale: null },
            split: {
              mode: "rolling",
              train_years: 1,
              test_years: 1,
              embargo_sessions: 0,
            },
          },
        })
      ).data,
      "create experiment",
    ).record;
    // 붙잡힌 첫 trial 의 run.
    let held = "";
    await expect
      .poll(async () => {
        const [state] = requireData(
          (
            await listExperimentTrials({
              client: apiClient,
              path: { experiment_id: experimentId },
            })
          ).data,
          "list experiment trials",
        );
        held = state?.attempts.at(-1)?.run_id ?? "";
        return held === "" ? null : await runStatus(held);
      })
      .toBe("running");

    // 같은 요청을 다시 보내도(새로고침한 뒤 다시 누르기) 새 실행이 생기지 않고 그 실행으로 간다.
    const request = requireData(
      (await getBacktestRequest({ client: apiClient, path: { run_id: held } }))
        .data,
      "read backtest request",
    );
    expect(await startRun(request)).toBe(held);

    // 내 실행 취소는 내 몫만 빼고, 실험이 쓰는 실행은 계속 돈다.
    await page.goto(`/research/backtests/${held}`);
    await page.reload();
    await page.getByRole("button", { name: "실행 취소" }).click();
    await expect(
      page.getByText(
        "실험이 이 실행을 함께 쓰고 있어 계속 돕니다. 멈추려면 실험 화면에서 실험을 취소하세요.",
      ),
    ).toBeVisible();
    expect(await runStatus(held)).toBe("running");

    // 실험을 취소해야 멈춘다.
    await page.goto(`/research/experiments`);
    await experimentRow(page, experimentId)
      .getByRole("button", { name: "취소" })
      .click();
    await experimentRow(page, experimentId)
      .getByRole("button", { name: "실험 취소 확인" })
      .click();
    await expect(experimentStatus(page, experimentId)).toContainText("취소됨");
    await expect
      .poll(() => runStatus(held), { timeout: 10_000 })
      .toBe("cancelled");
  },
);
