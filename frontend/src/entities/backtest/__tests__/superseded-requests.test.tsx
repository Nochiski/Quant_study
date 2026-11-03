import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import type { PropsWithChildren } from "react";
import { afterAll, afterEach, beforeAll, expect, it } from "vitest";

import type { BacktestRunSpec } from "../../../shared/api";
import {
  useBacktestSessionHoldings,
  useBacktestTrialPreview,
} from "../model/backtest-queries";

const API = "http://localhost:8000/api/v1";

// 답하지 않고 끊길 때만 닫히는 요청 — 밀려난 요청이 실제로 끊기는지 신호로 본다.
const signals: AbortSignal[] = [];
const hanging = ({ request }: { request: Request }) => {
  signals.push(request.signal);
  return new Promise<Response>((resolve) => {
    request.signal.addEventListener(
      "abort",
      () => resolve(new HttpResponse(null, { status: 499 })),
      { once: true },
    );
  });
};
const server = setupServer(
  http.post(`${API}/backtests/trial-preview`, hanging),
  http.get(`${API}/backtests/:runId/holdings/sessions/:session`, hanging),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  signals.length = 0;
});
afterAll(() => server.close());

const wrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
};

const request = (initialCash: number): BacktestRunSpec => ({
  strategy_source: {
    kind: "saved_revision",
    strategy_id: "s1",
    revision: 1,
    expected_spec_hash: "pinned",
  },
  initial_cash: initialCash,
});

it("aborts the trial preview of a run setting the user has already typed past", async () => {
  const { rerender } = renderHook(
    ({ spec }: { spec: BacktestRunSpec }) => useBacktestTrialPreview(spec),
    { initialProps: { spec: request(1) }, wrapper: wrapper() },
  );
  await waitFor(() => expect(signals).toHaveLength(1));

  rerender({ spec: request(10) });

  await waitFor(() => expect(signals).toHaveLength(2));
  expect(signals.map((signal) => signal.aborted)).toEqual([true, false]);
});

it("aborts the holdings of a session the user has already moved past", async () => {
  const { rerender } = renderHook(
    ({ session }: { session: string }) =>
      useBacktestSessionHoldings("run-1", session),
    { initialProps: { session: "2024-01-08" }, wrapper: wrapper() },
  );
  await waitFor(() => expect(signals).toHaveLength(1));

  rerender({ session: "2024-01-09" });

  await waitFor(() => expect(signals).toHaveLength(2));
  expect(signals.map((signal) => signal.aborted)).toEqual([true, false]);
});
