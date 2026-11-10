import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
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

const API = "http://localhost:8000/api/v1/stock-chart";
const KOSPI: ChartTarget = { kind: "index", code: "practice:KOSPI:코스피" };

const line = (id: string, values: Partial<ChartLineBody> = {}): ChartLineBody => ({
  line_id: id,
  instrument_kind: "index",
  instrument_code: KOSPI.code,
  timeframe: "week",
  kind: "hline",
  source: "manual",
  anchors: [{ bar_date: "2025-03-03", price: 2_500 }],
  levels: null,
  extend_right: true,
  memo: "",
  locked: false,
  visible: true,
  auto: null,
  created: null,
  created_at: "2026-11-06T00:00:00Z",
  updated_at: "2026-11-06T00:00:00Z",
  issue: null,
  paths: [],
  ...values,
});

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
    const { result } = renderHook(() => useLineVisibility(), { wrapper });

    act(() => {
      result.current.setVisible([line("a"), line("b")], false);
    });

    await waitFor(() => expect(result.current.failed).toBe(1));
    act(() => {
      result.current.dismiss();
    });
    expect(result.current.failed).toBe(0);
  });
});
