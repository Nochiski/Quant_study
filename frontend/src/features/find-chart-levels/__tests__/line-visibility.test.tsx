import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, delay, http } from "msw";
import { setupServer } from "msw/node";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import {
  chartLinesQuery,
  type ChartLineBody,
  type ChartLineEditBody,
  type ChartLinesBody,
  type ChartTarget,
} from "../../../entities/chart";
import { useLineVisibility } from "../model/use-line-visibility";
import { chartLineFixture as line } from "../../../shared/testing/chart-line";

const API = "http://localhost:8000/api/v1/stock-chart";
const KOSPI: ChartTarget = { kind: "index", code: "practice:KOSPI:코스피" };

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const setup = (lines: ChartLineBody[]) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: 0 } },
  });
  const body: ChartLinesBody = {
    context: {
      instrument: { kind: "index", code: KOSPI.code, name: "코스피", group: "KOSPI", tick_steps: [] },
      as_of: "2025-10-30",
      practice_data: true,
      current_price: 2500,
      notice: null,
    },
    lines,
    level_choices: [],
  };
  client.setQueryData(chartLinesQuery(KOSPI).queryKey, body);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
};

describe("useLineVisibility", () => {
  it("reports the lines whose visibility could not be saved when a bundle partly fails", async () => {
    // 수평 구간 한 벌(띠 둘)을 끈다 — 하나는 저장되고 하나는 거절되면 실패 1개를 알린다
    server.use(
      http.put(`${API}/lines/:id`, async ({ params, request }) => {
        if (params.id === "b")
          return HttpResponse.json(
            { detail: { code: "chart.line.not_found", message: "line not found — line_id=b" } },
            { status: 404 },
          );
        const edit = (await request.json()) as ChartLineEditBody;
        return HttpResponse.json(line(String(params.id), { visible: edit.visible }));
      }),
      http.get(`${API}/lines`, () => HttpResponse.json({ lines: [] })),
    );
    const { wrapper } = setup([line("a"), line("b")]);
    const { result } = renderHook(() => useLineVisibility(KOSPI), { wrapper });

    act(() => {
      result.current.setVisible([line("a"), line("b")], false);
    });

    // 실패 1개와 서버 사유(코드에 맞는 문장을 고를 오류)를 함께 든다
    await waitFor(() => expect(result.current.failure?.count).toBe(1));
    expect(result.current.failure?.error).toBeInstanceOf(Error);
    act(() => {
      result.current.dismiss();
    });
    expect(result.current.failure).toBeNull();
  });

  it("drops the notice when another instrument opens and ignores answers that arrive after it", async () => {
    // 늦게 거절되는 앞 종목의 응답이 새 종목 화면의 알림으로 새지 않는다
    server.use(
      http.put(`${API}/lines/:id`, async () => {
        await delay(30);
        return HttpResponse.json(
          { detail: { code: "chart.line.not_found", message: "line not found" } },
          { status: 404 },
        );
      }),
      http.get(`${API}/lines`, () => HttpResponse.json({ lines: [] })),
    );
    const { wrapper } = setup([line("a")]);
    const { result, rerender } = renderHook(({ target }) => useLineVisibility(target), {
      wrapper,
      initialProps: { target: KOSPI },
    });

    act(() => {
      result.current.setVisible([line("a")], false);
    });
    rerender({ target: { kind: "stock", code: "practice:005930" } });
    await new Promise((resolve) => setTimeout(resolve, 80));

    expect(result.current.failure).toBeNull();
  });
});
