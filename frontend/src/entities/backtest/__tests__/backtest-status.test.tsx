import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import { useBacktestStatus } from "../model/backtest-queries";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => { cleanup(); server.resetHandlers(); });
const Status = () => {
  const result = useBacktestStatus("missing");
  return <p role="status">{result.isError ? "조회 실패" : "조회 중"}</p>;
};
it("없는 실행의 상태 조회는 오류 뒤에 자동 폴링하지 않는다", async () => {
  let requests = 0;
  server.use(http.get("http://localhost:8000/api/v1/backtests/missing", () => {
    requests += 1;
    return HttpResponse.json({ detail: { code: "backtest.run.not_found" } }, { status: 404 });
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><Status /></QueryClientProvider>);
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("조회 실패"));
  await new Promise((resolve) => setTimeout(resolve, 650));
  expect(requests).toBe(1);
  client.clear();
});
