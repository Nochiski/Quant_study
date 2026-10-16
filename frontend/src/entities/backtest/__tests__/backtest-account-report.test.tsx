import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, delay, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import type { AccountReport } from "../../../shared/api";
import { useBacktestAccountReport } from "../model/backtest-queries";
import { BacktestAccountReport } from "../ui/backtest-account-report";

const URL = "http://localhost:8000/api/v1/backtests/:runId/account-report";
/** 서버가 계산한 알려진 값을 보이는 경계 테스트다. 화면에서 산식을 재현하지 않는다. */
const report = (runId = "run-1"): AccountReport => ({
  run_id: runId,
  evaluation_basis: "initial_capital",
  interpretation:
    "평가액은 모두 팔아 돌려받는 현금이 아닙니다. 비용은 기반 결과에 반영됩니다.",
  status: "available",
  detail: null,
  summary: {
    initial_cash: 100_000,
    final_equity: 105_123.45,
    final_cash: 123.45,
    pnl: 5_123.45,
    total_return: 0.0512345,
    first_snapshot_date: "2026-01-05",
    last_snapshot_date: "2026-02-03",
    snapshot_count: 22,
  },
  provenance: {
    run_fingerprint: "f".repeat(64),
    engine_version: "test",
    strategy_hash: "a".repeat(64),
    data_snapshot_id: "snapshot",
    target_tape_hash: "b".repeat(64),
    metric_registry_version: "v1",
    environment_hash: "e".repeat(64),
    strategy_provenance: {
      kind: "inline_draft",
      spec_hash: "a".repeat(64),
      schema_version: "1.2",
    },
    schema_version: "backtest-run-v3",
    factor_evaluation_version: null,
  },
  warnings: [],
});
const server = setupServer(
  http.get(URL, ({ params }) =>
    HttpResponse.json(report(String(params.runId))),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());
const Harness = ({
  runId,
  completed = true,
}: {
  runId: string;
  completed?: boolean;
}) => {
  const query = useBacktestAccountReport(runId, completed);
  return completed ? (
    <BacktestAccountReport
      report={query.data}
      error={query.error}
      loading={query.isPending}
      retry={() => {
        void query.refetch();
      }}
    />
  ) : null;
};
const mount = (runId = "run-1", completed = true) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const element = (id: string, done = true) => (
    <QueryClientProvider client={client}>
      <Harness runId={id} completed={done} />
    </QueryClientProvider>
  );
  return { ...render(element(runId, completed)), element };
};
const field = (name: string) =>
  screen.getByText(name, { selector: "dt" }).parentElement!;

describe("초기자본 기준 계좌 요약의 통신과 표시", () => {
  it("서버의 비용 반영 후 값과 현금·평가액·실제 기록일을 구별하고 지표 기준 차이를 설명한다", async () => {
    mount();
    await screen.findByText("계좌 요약 계산 완료");
    expect(screen.getByText("계좌 요약 데이터 경고 (0건)")).toBeVisible();
    expect(field("초기자본 (시작조건)")).toHaveTextContent("₩100,000");
    expect(field("초기자본 (시작조건)")).not.toHaveTextContent("2026");
    expect(field("최종 평가액")).toHaveTextContent("₩105,123.45");
    expect(field("마지막 기록의 현금")).toHaveTextContent("₩123.45");
    expect(field("비용 반영 후 손익")).toHaveTextContent("₩5,123.45");
    expect(field("초기자본 대비 수익률")).toHaveTextContent("5.12%");
    expect(field("첫 기록일")).toHaveTextContent("2026-01-05");
    expect(field("마지막 기록일")).toHaveTextContent("2026-02-03");
    expect(screen.getByText(report().interpretation!)).toBeVisible();
    expect(
      screen.getByText(/전체 구간\(FULL\)의 총수익률은 첫 계좌 기록/u),
    ).toBeVisible();
    await userEvent.setup().click(screen.getByText("계좌 요약 재현성 정보"));
    expect(screen.getByText("f".repeat(64))).toBeVisible();
  });

  it("데이터 경고는 건수만 먼저 보이고 펼치면 서버 코드와 원문을 순서대로 모두 읽는다", async () => {
    const body = report();
    body.warnings = [
      { code: "data.first_warning", message: "첫 번째 서버 원문 경고" },
      { code: "data.zero_count", message: "경고 대상 종목 수: 0" },
    ];
    server.use(http.get(URL, () => HttpResponse.json(body)));
    mount();
    const summary = await screen.findByText("계좌 요약 데이터 경고 (2건)");
    expect(summary).toBeVisible();
    for (const warning of body.warnings)
      expect(screen.getByText(warning.message)).not.toBeVisible();
    const user = userEvent.setup();
    await user.click(summary);
    const warnings = screen.getAllByRole("listitem");
    expect(warnings).toHaveLength(2);
    for (const [index, warning] of body.warnings.entries()) {
      expect(within(warnings[index]).getByText(warning.code)).toBeVisible();
      expect(within(warnings[index]).getByText(warning.message)).toBeVisible();
    }
    await user.click(summary);
    for (const warning of body.warnings)
      expect(screen.getByText(warning.message)).not.toBeVisible();
  });

  it.each([0, -12.5])(
    "유효한 현금·평가액 %s을 결측으로 바꾸지 않는다",
    async (value) => {
      const body = report();
      body.summary = {
        ...body.summary!,
        final_cash: value,
        final_equity: value,
        pnl: -100_000,
        total_return: -1,
      };
      server.use(http.get(URL, () => HttpResponse.json(body)));
      mount();
      await screen.findByText("계좌 요약 계산 완료");
      for (const name of ["최종 평가액", "마지막 기록의 현금"])
        expect(field(name)).toHaveTextContent(`₩${value}`);
      expect(field("초기자본 대비 수익률")).toHaveTextContent("-100.00%");
    },
  );

  it("유한한 큰 서버 수익률을 Infinity나 NaN으로 표시하지 않는다", async () => {
    const body = report();
    body.summary = { ...body.summary!, total_return: 1e308 };
    server.use(http.get(URL, () => HttpResponse.json(body)));
    mount();
    await screen.findByText("계좌 요약 계산 완료");
    expect(field("초기자본 대비 수익률")).not.toHaveTextContent(
      /Infinity|NaN|∞/u,
    );
    expect(field("초기자본 대비 수익률")).toHaveTextContent(/\d.*%/u);
  });

  it.each(["empty_snapshots", "invalid_input"] as const)(
    "%s이면 서버 사유만 보이고 숫자를 만들지 않는다",
    async (status) => {
      server.use(
        http.get(URL, () =>
          HttpResponse.json({
            ...report(),
            status,
            summary: null,
            detail: "서버가 기록을 확인할 수 없습니다.",
          }),
        ),
      );
      mount();
      expect(
        await screen.findByText("서버가 기록을 확인할 수 없습니다."),
      ).toBeVisible();
      expect(screen.queryByText("최종 평가액")).toBeNull();
      expect(screen.queryByText("₩0")).toBeNull();
      expect(screen.queryByRole("img")).toBeNull();
    },
  );

  it("503 오류는 다시 불러오기로 복구한다", async () => {
    let requests = 0;
    server.use(
      http.get(URL, () =>
        ++requests === 1
          ? HttpResponse.json({}, { status: 503 })
          : HttpResponse.json(report()),
      ),
    );
    mount();
    await userEvent
      .setup()
      .click(
        await screen.findByRole("button", { name: "계좌 요약 다시 불러오기" }),
      );
    expect(await screen.findByText("계좌 요약 계산 완료")).toBeVisible();
    expect(requests).toBe(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("연결 오류에는 계좌 보고서만 다시 읽는 동작을 제공한다", async () => {
    server.use(http.get(URL, () => HttpResponse.error()));
    mount();
    expect(
      await screen.findByRole("button", { name: "계좌 요약 다시 불러오기" }),
    ).toBeEnabled();
  });

  it.each([
    [404, "backtest.run.not_found", "이 실행을 찾을 수 없습니다."],
    [409, "backtest.result.not_ready", "이 실행은 아직 결과가 없습니다."],
    [
      410,
      "backtest.result.unreadable",
      "이 실행의 결과 파일을 읽을 수 없습니다.",
    ],
  ] as const)(
    "영구 오류 %s에는 재시도 버튼과 자동 재요청이 없다",
    async (status, code, message) => {
      let requests = 0;
      server.use(
        http.get(URL, () => {
          requests++;
          return HttpResponse.json(
            {
              detail: {
                code,
                message: "기록된 서버 사유",
              },
            },
            { status },
          );
        }),
      );
      mount();
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(message);
      expect(within(alert).getByText("서버 사유")).toBeVisible();
      expect(
        screen.queryByRole("button", { name: "계좌 요약 다시 불러오기" }),
      ).toBeNull();
      expect(requests).toBe(1);
    },
  );

  it("실행 전환 동안 앞 실행의 보고서를 보이지 않으며 완료 전에는 요청하지 않는다", async () => {
    const requests: string[] = [];
    server.use(
      http.get(URL, async ({ params }) => {
        requests.push(String(params.runId));
        if (params.runId === "run-2") await delay(30);
        return HttpResponse.json({
          ...report(String(params.runId)),
          interpretation: `보고서 ${String(params.runId)}`,
        });
      }),
    );
    const mounted = mount("run-1", false);
    expect(requests).toEqual([]);
    mounted.rerender(mounted.element("run-1"));
    expect(await screen.findByText("보고서 run-1")).toBeVisible();
    mounted.rerender(mounted.element("run-2"));
    expect(screen.queryByText("보고서 run-1")).toBeNull();
    expect(await screen.findByText("보고서 run-2")).toBeVisible();
    await waitFor(() => expect(requests).toEqual(["run-1", "run-2"]));
  });
});
