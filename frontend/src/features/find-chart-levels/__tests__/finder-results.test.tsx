import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, delay, http } from "msw";
import { setupServer } from "msw/node";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import type { ChartChannelsBody, ChartRegimeBody, ChartTarget } from "../../../entities/chart";
import { useLevelFinder } from "..";

const API = "http://localhost:8000/api/v1/stock-chart";
const KOSPI: ChartTarget = { kind: "index", code: "practice:KOSPI:코스피" };

// 두 판정 구간: W1은 상승, W2는 하락으로 판정된다
const W1 = { start: "2023-06-19", end: "2023-09-11" };
const W2 = { start: "2024-10-07", end: "2025-04-21" };

const regimeOf = (window: { start: string; end: string }, direction: "up" | "down") =>
  ({
    window: { ...window, bars: 60, source: "user" },
    verdict: direction,
    channel_direction: direction,
    suggestions: [],
  }) as unknown as ChartRegimeBody;

const channelsOf = (start: string, direction: string) =>
  ({
    direction,
    candidates: [0, 1].map((rank) => ({
      drawing: {
        anchors: [
          { bar_date: start, price: 100 + rank },
          { bar_date: "2025-01-02", price: 90 },
          { bar_date: "2025-02-03", price: 80 },
        ],
        paths: [],
      },
      outside_ratio: 0,
      span_bars: 40,
      width_atr: 2,
      created: "2025-02-17",
    })),
    record: { algorithm_version: "chart_analysis@1", params: {}, chosen_by: "recommendation" },
    anchor_choices: [],
  }) as unknown as ChartChannelsBody;

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/** 요청을 적어 두고, W2 판정과 W2 채널은 늦게 답한다(그동안 앞 응답이 placeholder로 보인다). */
const answer = () => {
  const channelRequests: { start: string | null; direction: string | null }[] = [];
  server.use(
    http.get(`${API}/regime`, async ({ request }) => {
      const start = new URL(request.url).searchParams.get("window_start");
      if (start === W2.start) {
        await delay(80);
        return HttpResponse.json(regimeOf(W2, "down"));
      }
      return HttpResponse.json(regimeOf(W1, "up"));
    }),
    http.get(`${API}/channels`, async ({ request }) => {
      const params = new URL(request.url).searchParams;
      const start = params.get("window_start");
      channelRequests.push({ start, direction: params.get("direction") });
      if (start === W2.start) await delay(80);
      return HttpResponse.json(channelsOf(start ?? "", params.get("direction") ?? ""));
    }),
  );
  return channelRequests;
};

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    {children}
  </QueryClientProvider>
);

describe("finder results while a new window is being judged", () => {
  it("drops the old recommendation until the new judgement arrives, so the old direction is never asked", async () => {
    const requests = answer();
    const { result } = renderHook(() => useLevelFinder(KOSPI, "day"), { wrapper });
    act(() =>
      result.current.dispatch({ type: "openCreate", tool: "fib_channel", seed: { window: W1 }, returnKey: "k" }),
    );
    await waitFor(() => expect(result.current.results.ready).toBe(true));
    expect(result.current.results.direction).toBe("up");

    // 구간을 W2로 옮겼다 — W2 판정(하락)이 오기 전에는 W1의 추천(상승)으로 W2 채널을 묻지 않고, 추가할 수 없다
    act(() => result.current.dispatch({ type: "edit", input: { window: W2 } }));
    expect(result.current.results.recommended).toBeNull();
    expect(result.current.results.direction).toBeNull();
    expect(result.current.results.ready).toBe(false);
    // 앞 판정을 보이며 다시 찾는 중이라고 알린다
    expect(result.current.results.refreshing).toBe(true);
    await waitFor(() => expect(result.current.results.direction).toBe("down"));
    await waitFor(() => expect(result.current.results.ready).toBe(true));
    expect(result.current.results.refreshing).toBe(false);
    expect(requests.filter((request) => request.start === W2.start)).toEqual([{ start: W2.start, direction: "down" }]);
  });

  it("neither draws nor lets a pick land on the old candidates while the new ones load", async () => {
    answer();
    const { result } = renderHook(() => useLevelFinder(KOSPI, "day"), { wrapper });
    act(() =>
      result.current.dispatch({
        type: "openCreate",
        tool: "fib_channel",
        seed: { window: W1, direction: "up" },
        returnKey: "k",
      }),
    );
    await waitFor(() => expect(result.current.results.ready).toBe(true));
    // 직접 고른 방향이라 W2 판정을 기다리지 않고 채널을 묻는다 — 그 답이 오기 전에는 앞 후보가 보여도 고를 수 없다
    expect(result.current.results.previews[0].drawing.anchors[0].bar_date).toBe(W1.start);
    act(() => result.current.dispatch({ type: "edit", input: { window: W2 } }));
    await waitFor(() => expect(result.current.results.channels.isPlaceholderData).toBe(true));
    expect(result.current.results.ready).toBe(false);
    // 앞 구간의 채널은 새 구간 위에 미리보기로 남지 않는다
    expect(result.current.results.previews).toEqual([]);
    await waitFor(() => expect(result.current.results.ready).toBe(true));
    expect(result.current.results.previews[0].drawing.anchors[0].bar_date).toBe(W2.start);
  });
});
