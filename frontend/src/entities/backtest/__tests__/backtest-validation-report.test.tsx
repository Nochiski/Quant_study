import { focusManager, onlineManager, QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import type { RunValidationReport } from "../../../shared/api";
import { useBacktestValidationReport } from "../model/backtest-queries";
import { BacktestValidationReport } from "../ui/backtest-validation-report";
import { BacktestMetricTable } from "../ui/backtest-metrics";

const URL = "http://localhost:8000/api/v1/backtests/:runId/validation-report";
/** 응답에 적힌 알려진 수치를 대조하며 DSR 공식을 테스트 화면에서 복제하지 않는다. */
const report = (runId = "run-1"): RunValidationReport => ({
  run_id: runId,
  run_fingerprint: `${runId}-${"f".repeat(64)}`,
  metric_registry_version: "metric-registry-v5",
  observed_at: "2026-10-17T12:34:56Z",
  psr: { metric_id: "probabilistic_sharpe", scope: "full", sample_count: 889, value: 0.9999, unavailable_reason: null },
  dsr: {
    value: 0.610123456789,
    unavailable_reason: null,
    lineage_id: "lineage-1",
    merged_lineage_ids: ["merged-1"],
    trial_count: 238,
    representative_count: 237,
    trial_sharpe_variance: 0.000248015873015873,
    expected_maximum_sharpe: 0.04123456789,
    session_sharpe: 0.054176,
    session_standard_error: 0.027394,
    annualization_days: 260,
    representative_registry_versions: ["metric-registry-v4", "metric-registry-v5"],
    unknown_registry_count: 1,
    registry_mixed: true,
  },
});
const server = setupServer(http.get(URL, ({ params }) => HttpResponse.json(report(String(params.runId)))));
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  onlineManager.setOnline(true);
  focusManager.setFocused(undefined);
  server.resetHandlers();
});
afterAll(() => server.close());

const Harness = ({ runId, completed = true }: { runId: string; completed?: boolean }) => {
  const query = useBacktestValidationReport(runId, completed);
  return completed ? <BacktestValidationReport report={query.data} error={query.error} loading={query.isPending} fetching={query.isFetching} refresh={() => { void query.refresh(); }} /> : null;
};
const mount = (runId = "run-1", completed = true) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const element = (id: string, done = true) => <QueryClientProvider client={client}><Harness runId={id} completed={done} /></QueryClientProvider>;
  return { ...render(element(runId, completed)), element, client };
};
const evidenceField = (label: string) => screen.getByText(label, { selector: "dt" }).parentElement!;

describe("실행 검증 카드의 wire 조회와 읽기", () => {
  it("서버 값·현재 원장 시점·혼합 경고를 보이고 정확한 근거를 펼쳐 읽는다", async () => {
    mount();
    const psr = await screen.findByRole("region", { name: "샤프 기준 초과 통계(PSR)" });
    expect(psr).toHaveTextContent("99.99%");
    expect(psr).not.toHaveTextContent(">99.99%");
    expect(psr).toHaveTextContent("889");
    const dsr = screen.getByRole("region", { name: "시도 횟수 보정 통계(DSR)" });
    expect(dsr).toHaveTextContent("61.01%");
    expect(dsr).toHaveTextContent("계열 시도 수 N: 238");
    expect(dsr).toHaveTextContent("대표 샤프 자료 수: 237");
    expect(screen.getByText(/조회 시점의 계열 원장 기준입니다/u)).toBeVisible();
    expect(screen.getByText(/판본이 섞여 있습니다/u)).toBeVisible();
    expect(screen.getByText(/판본을 알 수 없는 대표 자료/u)).toBeVisible();
    expect(document.querySelector("time")).toHaveAttribute("datetime", "2026-10-17T12:34:56Z");
    expect(document.querySelector("time")).toHaveTextContent("UTC");
    expect(screen.getByText("0.610123456789")).not.toBeVisible();
    const summary = screen.getByText("계산 근거", { selector: "summary" });
    await userEvent.setup().click(summary);
    expect(evidenceField("DSR 원래 수치")).toHaveTextContent("0.610123456789");
    expect(evidenceField("PSR 원래 수치")).toHaveTextContent("0.9999");
    expect(evidenceField("실행의 연환산 거래일")).toHaveTextContent("260");
    expect(evidenceField("실행 지문")).toHaveTextContent(report().run_fingerprint);
    expect(screen.getByText(/처음 완료된 실행에서 정합니다/u)).toBeVisible();
  });

  it("포화된 통계는 카드에서만 상한 표시하고 원래 수치 1을 읽을 수 있다", async () => {
    const body = report();
    body.psr!.value = 1;
    server.use(http.get(URL, () => HttpResponse.json(body)));
    mount();
    expect(await screen.findByText(">99.99%", { exact: true })).toBeVisible();
    render(<BacktestMetricTable definitions={[{ metric_id: "probabilistic_sharpe", label: "Probabilistic Sharpe ratio", category: "risk_adjusted", unit: "percent", higher_is_better: true, nullable: true }]} metrics={[body.psr!]} />);
    expect(within(screen.getByRole("region", { name: "Raw metric table" })).getByText("100.00%", { exact: true })).toBeVisible();
    await userEvent.setup().click(screen.getByText("계산 근거"));
    expect(evidenceField("PSR 원래 수치").querySelector("dd")).toHaveTextContent(/^1$/u);
  });

  it("PSR 미기록과 사용 불가 사유를 구별하고 값을 0으로 채우지 않는다", async () => {
    const body = report();
    body.psr = null;
    server.use(http.get(URL, () => HttpResponse.json(body)));
    mount();
    expect(await screen.findByText("이 실행에 PSR 기록이 없습니다.")).toBeVisible();
    expect(within(screen.getByRole("region", { name: "샤프 기준 초과 통계(PSR)" })).queryByText("0.00%")).toBeNull();
    body.psr = { ...report().psr!, value: null, unavailable_reason: "two_valued_returns" };
    await userEvent.setup().click(screen.getByRole("button", { name: "원장 기준 다시 조회" }));
    await waitFor(() => expect(screen.queryByText("이 실행에 PSR 기록이 없습니다.")).toBeNull());
    expect(screen.getByRole("region", { name: "샤프 기준 초과 통계(PSR)" })).toHaveTextContent("값 없음");
    expect(screen.getByText("수익률이 두 가지 값만 나와 오차를 잴 수 없습니다")).toBeVisible();
  });

  it.each([
    ["no_lineage", "계열이 없는 실행이라 시도 횟수 보정 통계가 없습니다."],
    ["no_counted_trials", "계열에 통계 계산에 사용할 시도 기록이 없습니다."],
    ["sharpe_unavailable", "이 실행의 전체 구간 샤프가 없어 계산할 수 없습니다."],
    ["standard_error_unavailable", "이 실행의 샤프 표준오차가 없어 계산할 수 없습니다."],
    ["nonpositive_standard_error", "샤프 표준오차가 양수가 아니어서 계산할 수 없습니다."],
    ["insufficient_representatives", "시도 대표 샤프 자료가 부족해 분산을 계산할 수 없습니다."],
  ] satisfies [NonNullable<RunValidationReport["dsr"]["unavailable_reason"]>, string][])("DSR 사유 %s를 서버 응답대로 표시한다", async (reason, message) => {
    const body = report();
    body.dsr.value = null;
    body.dsr.unavailable_reason = reason;
    server.use(http.get(URL, () => HttpResponse.json(body)));
    mount();
    expect(await screen.findByText(message)).toBeVisible();
    const dsr = screen.getByRole("region", { name: "시도 횟수 보정 통계(DSR)" });
    expect(dsr).toHaveTextContent("값 없음");
    expect(dsr).not.toHaveTextContent("0.00%");
  });

  it.each([503, 0])("복구 가능한 오류 %s는 명시적 조회로 복구하고 시점과 N을 갱신한다", async (status) => {
    let requests = 0;
    server.use(http.get(URL, () => {
      requests++;
      if (requests === 1) return status === 0 ? HttpResponse.error() : HttpResponse.json({}, { status });
      const body = report();
      body.dsr.trial_count = 239;
      body.observed_at = "2026-10-17T13:00:00Z";
      return HttpResponse.json(body);
    }));
    mount();
    await userEvent.setup().click(await screen.findByRole("button", { name: "원장 기준 다시 조회" }));
    await waitFor(() => expect(document.querySelector("time")).toHaveAttribute("datetime", "2026-10-17T13:00:00Z"));
    expect(screen.getByText("계열 시도 수 N: 239")).toBeVisible();
    expect(requests).toBe(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("410은 focus·재접속·재마운트로 재조회하지 않고 재시도 버튼도 없다", async () => {
    let requests = 0;
    server.use(http.get(URL, () => {
      requests++;
      return HttpResponse.json({ detail: { code: "backtest.result.unreadable", message: "보존한 서버 사유" } }, { status: 410 });
    }));
    const mounted = mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("이 실행의 결과 파일을 읽을 수 없습니다.");
    expect(screen.queryByRole("button", { name: "원장 기준 다시 조회" })).toBeNull();
    const PositiveControl = () => {
      const query = useQuery({ queryKey: ["reconnect-control"], queryFn: () => { controls++; return Promise.resolve("정상 조회"); }, staleTime: 0 });
      return <p>{query.data}</p>;
    };
    let controls = 0;
    const control = render(<QueryClientProvider client={mounted.client}><PositiveControl /></QueryClientProvider>);
    await screen.findByText("정상 조회");
    await act(async () => {
      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    await waitFor(() => expect(controls).toBeGreaterThan(1));
    mounted.rerender(<QueryClientProvider client={mounted.client}><p>다른 화면</p></QueryClientProvider>);
    mounted.rerender(mounted.element("run-1"));
    expect(screen.getByRole("alert")).toHaveTextContent("이 실행의 결과 파일을 읽을 수 없습니다.");
    expect(requests).toBe(1);
    control.unmount();
  });

  it.each([503, 410])("성공 뒤 재조회 %s 실패는 마지막 값과 파일 손실을 구분한다", async (status) => {
    let requests = 0;
    server.use(http.get(URL, () => {
      requests++;
      return requests === 1 ? HttpResponse.json(report()) : HttpResponse.json({ detail: { code: status === 410 ? "backtest.result.unreadable" : "unavailable" } }, { status });
    }));
    mount();
    await screen.findByRole("region", { name: "시도 횟수 보정 통계(DSR)" });
    await userEvent.setup().click(screen.getByRole("button", { name: "원장 기준 다시 조회" }));
    await screen.findByRole("alert");
    if (status === 503) {
      expect(screen.getByText(/아래 수치는 마지막으로 확인한 값입니다/u)).toBeVisible();
      expect(screen.getByRole("region", { name: "시도 횟수 보정 통계(DSR)" })).toHaveTextContent("61.01%");
      expect(document.querySelector("time")).toHaveAttribute("datetime", report().observed_at);
    } else {
      expect(screen.queryByRole("region", { name: "시도 횟수 보정 통계(DSR)" })).toBeNull();
      expect(screen.queryByRole("button", { name: "원장 기준 다시 조회" })).toBeNull();
    }
    expect(requests).toBe(2);
  });

  it("완료 전에는 요청하지 않고 실행 전환 동안 이전 값이나 지연 응답을 보이지 않는다", async () => {
    const requests: string[] = [];
    const times = { "run-1": "2026-10-17T12:00:00Z", "run-2": "2026-10-17T13:00:00Z", "run-3": "2026-10-17T14:00:00Z" };
    let delayedDelivered = false;
    server.use(http.get(URL, async ({ params }) => {
      const id = String(params.runId);
      requests.push(id);
      if (id === "run-2") await delay(30);
      const body = report(id);
      body.observed_at = times[id as keyof typeof times];
      if (id === "run-2") delayedDelivered = true;
      return HttpResponse.json(body);
    }));
    const mounted = mount("run-1", false);
    expect(requests).toEqual([]);
    mounted.rerender(mounted.element("run-1"));
    await waitFor(() => expect(document.querySelector("time")).toHaveAttribute("datetime", times["run-1"]));
    mounted.rerender(mounted.element("run-2"));
    expect(document.querySelector("time")).toBeNull();
    await waitFor(() => expect(requests).toContain("run-2"));
    mounted.rerender(mounted.element("run-3"));
    await waitFor(() => expect(document.querySelector("time")).toHaveAttribute("datetime", times["run-3"]));
    await waitFor(() => expect(delayedDelivered).toBe(true));
    expect(document.querySelector("time")).toHaveAttribute("datetime", times["run-3"]);
    expect(requests).toEqual(["run-1", "run-2", "run-3"]);
  });
});
