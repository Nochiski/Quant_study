import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { BacktestRunResult } from "../../../shared/api";
import { BacktestOrderEvents } from "../ui/backtest-order-events";

type Artifacts = BacktestRunResult["artifacts"];
const artifacts = (updates?: Artifacts["order_updates"]): Artifacts => ({
  orders: [{ order_id: "order-1", security_id: "KRX:005930", decision_id: "d1", session: "2026-10-12", side: "buy", quantity: "5", order_type: "market", time_in_force: "gtc" }],
  order_updates: updates,
  fills: [], trades: [], positions: [], costs: [], snapshots: [],
});
afterEach(cleanup);

describe("주문 상태 원본 기록", () => {
  it.each([undefined, null])("옛 결과의 미기록(%s)을 이벤트 0건과 구분한다", (updates) => {
    render(<BacktestOrderEvents artifacts={artifacts(updates)} />);
    expect(screen.getByRole("region", { name: "주문 상태 기록" })).toHaveTextContent("이전 형식 결과에서는 미체결 사유를 확인할 수 없습니다");
    expect(screen.queryByText(/0건/)).not.toBeInTheDocument();
  });

  it("새 결과의 빈 목록은 0건으로 표시한다", () => {
    render(<BacktestOrderEvents artifacts={artifacts([])} />);
    expect(screen.getByText("기록된 주문 상태 이벤트가 없습니다 (0건).")).toBeVisible();
    expect(screen.queryByText(/이전 형식/)).not.toBeInTheDocument();
  });

  it("원본 순서·상태·수치·메시지를 보존하고 사유 코드로만 이벤트를 집계한다", async () => {
    const user = userEvent.setup();
    render(<BacktestOrderEvents artifacts={artifacts([
      { session: "2026-10-12", order_id: "order-1", status: "open", reason_code: "liquidity_cap_zero", cap_volume: 0, detail: "backend verbatim" },
      { session: "2026-10-13", order_id: "order-1", status: "open", reason_code: "liquidity_cap_zero", cap_volume: 1 },
      { session: "2026-10-13", order_id: "order-1", status: "filled", detail: "liquidity_cap_zero cap_volume=0" },
      { session: "2026-10-13", order_id: "orphan", status: "future_state", reason_code: "future_reason", cap_volume: 17, detail: "future detail" },
    ])} />);
    expect(screen.getByText("주문 상태 이벤트 4건")).toBeVisible();
    expect(screen.getByText("유동성 한도 0주 기록 2건 (이벤트 기준)")).toBeVisible();
    await user.click(screen.getByText("주문 상태 원본 보기"));
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(4);
    expect(screen.getByRole("columnheader", { name: "기준 거래량 (주)" })).toBeVisible();
    // 참여율 적용 뒤 정수 수량이 0이어도 견적의 원본 기준 거래량은 1일 수 있다. UI는 재계산하지 않는다.
    expect(within(rows[1]!).getAllByRole("cell")[5]).toHaveTextContent("1");
    expect(rows[1]).toHaveTextContent("유동성 한도 0주");
    expect(rows[0]).toHaveTextContent("KRX:005930");
    expect(rows[0]).toHaveTextContent("대기");
    expect(rows[0]).toHaveTextContent("backend verbatim");
    expect(within(rows[0]!).getAllByRole("cell")[5]).toHaveTextContent("0");
    expect(rows[2]).toHaveTextContent("체결 완료");
    expect(rows[2]).toHaveTextContent("liquidity_cap_zero cap_volume=0");
    expect(rows[3]).toHaveTextContent("future_state");
    expect(rows[3]).toHaveTextContent("future_reason");
    expect(rows[3]).toHaveTextContent("17");
    expect(within(rows[3]!).getAllByRole("cell")[2]).toHaveTextContent("—");
  });
});
