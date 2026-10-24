import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import type {
  HoldingsOverview,
  SecurityTradesReport,
  SessionHoldingsReport,
} from "../../../shared/api";
import { BacktestHoldings } from "../ui/backtest-holdings";

const BASE = "http://localhost:8000/api/v1/backtests/:runId/holdings";
const samsung = { security_id: "005930:1", ticker: "005930", name: "삼성전자" };
const unknown = { security_id: "999999:1", ticker: null, name: null };

const overview: HoldingsOverview = {
  run_id: "run-1",
  first_session: "2024-01-02",
  last_session: "2024-03-29",
  fill_count: 3,
  final_holding_count: 1,
  trade_sessions: [
    { session: "2024-01-02", holding_count: 2, opened_count: 2, closed_count: 0, fill_count: 2 },
    { session: "2024-02-01", holding_count: 1, opened_count: 0, closed_count: 1, fill_count: 1 },
  ],
  securities: [
    {
      security: samsung,
      holding_periods: [{ start: "2024-01-02", end: "2024-03-29", open: true }],
      fill_count: 1,
      realized_pnl: 0,
      final_quantity: "10",
      final_unrealized_return: 0.05,
    },
    {
      security: unknown,
      holding_periods: [{ start: "2024-01-02", end: "2024-01-31", open: false }],
      fill_count: 2,
      realized_pnl: -1200,
      final_quantity: null,
      final_unrealized_return: null,
    },
  ],
};

const sessionReport = (session: string): SessionHoldingsReport =>
  session === "2024-01-02" || session === "2024-02-01"
    ? {
        run_id: "run-1",
        session,
        status: "available",
        detail: null,
        holdings: {
          session,
          equity: 1_000_000,
          cash: 200_000,
          cash_weight: 0.2,
          holdings: [
            {
              security: samsung,
              quantity: "10",
              market_value: 800_000,
              weight: 0.8,
              unrealized_return: session === "2024-01-02" ? 0 : 0.05,
              change: session === "2024-01-02" ? "opened" : "held",
              filled_quantity: session === "2024-01-02" ? "10" : null,
            },
          ],
          closed:
            session === "2024-02-01"
              ? [{ security: unknown, change: "sold", previous_quantity: "5", realized_pnl: -1200 }]
              : [],
          totals: { buy_count: 2, buy_notional: 900_000, sell_count: 0, sell_notional: 0, fees: 135, taxes: 0 },
        },
      }
    : {
        run_id: "run-1",
        session,
        status: "session_not_recorded",
        detail: `그날은 이 실행의 계좌 기록이 없습니다(거래일이 아니거나 실행 기간 밖). session=${session}`,
        holdings: null,
      };

const securityReport: SecurityTradesReport = {
  run_id: "run-1",
  security: unknown,
  status: "available",
  detail: null,
  sessions: [
    {
      session: "2024-02-01",
      change: "sold",
      quantity_after: null,
      realized_pnl: -1200,
      fills: [
        { fill_id: "f-1", side: "sell", quantity: "5", price: 9_000, notional: 45_000, fee: 7, sell_tax: 81 },
      ],
    },
  ],
};

const requested: string[] = [];
const server = setupServer(
  http.get(BASE, () => HttpResponse.json(overview)),
  http.get(`${BASE}/sessions/:session`, ({ params }) => {
    requested.push(`session:${String(params.session)}`);
    return HttpResponse.json(sessionReport(String(params.session)));
  }),
  http.get(`${BASE}/securities/:securityId`, ({ params }) => {
    requested.push(`security:${String(params.securityId)}`);
    return HttpResponse.json(securityReport);
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  requested.length = 0;
});
afterAll(() => server.close());

const renderHoldings = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <BacktestHoldings runId="run-1" enabled />
    </QueryClientProvider>,
  );

describe("BacktestHoldings", () => {
  it("opens on the latest trading day and reads only the day the user picks", async () => {
    const user = userEvent.setup();
    renderHoldings();

    expect(await screen.findByRole("heading", { name: /2024-02-01 장 마감 보유 1종목/ })).toBeInTheDocument();
    const closed = screen.getByRole("region", { name: "모두 정리한 종목 표" });
    // 이름을 모르는 종목은 id 를 이름 자리에 둔다.
    expect(within(closed).getByText("999999:1")).toBeInTheDocument();
    expect(within(closed).getByText("전량 매도")).toBeInTheDocument();
    expect(requested).toEqual(["session:2024-02-01"]);

    await user.click(screen.getByRole("button", { name: /2024-01-02/ }));
    const table = await screen.findByRole("region", { name: "보유 종목 표" });
    await waitFor(() => expect(within(table).getByText("신규")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /2024-01-02/ })).toHaveAttribute("aria-pressed", "true");
    expect(requested).toEqual(["session:2024-02-01", "session:2024-01-02"]);
  });

  it("moves between trading days with the arrow keys", async () => {
    const user = userEvent.setup();
    renderHoldings();
    const latest = await screen.findByRole("button", { name: /2024-02-01/ });
    latest.focus();

    await user.keyboard("{ArrowUp}");

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /2024-01-02/ })).toHaveAttribute("aria-pressed", "true"),
    );
    expect(screen.getByRole("button", { name: /2024-01-02/ })).toHaveFocus();
  });

  it("shows the server's reason for a day without an account record", async () => {
    renderHoldings();
    const input = await screen.findByLabelText("아무 날의 보유 보기");

    // 날짜 고르개는 완성된 날짜 하나를 값으로 넣는다.
    fireEvent.change(input, { target: { value: "2024-01-06" } });

    expect(await screen.findByText(/session=2024-01-06/)).toBeInTheDocument();
    // 매매일이 아닌 날은 목록에서 눌린 줄이 없다.
    expect(screen.getByRole("button", { name: /2024-01-02/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("reads fills only for the security the user selects", async () => {
    const user = userEvent.setup();
    renderHoldings();
    await user.click(await screen.findByRole("tab", { name: "종목별 매매 내역" }));
    await user.click(screen.getByRole("button", { name: /999999:1, 정리함, 체결 2건/ }));

    const fills = await screen.findByRole("region", { name: "체결 기록 표" });
    expect(within(fills).getByText("매도")).toBeInTheDocument();
    expect(within(fills).getByText("₩45,000")).toBeInTheDocument();
    expect(requested.filter((item) => item.startsWith("security:"))).toEqual([
      "security:005930:1",
      "security:999999:1",
    ]);
  });
});
