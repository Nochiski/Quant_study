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
import { useChartDrawing } from "..";

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

describe("line edits", () => {
  it("keeps both of two quick edits because the second PUT is built from the first answer", async () => {
    // 칸 전체 PUT이라 겹쳐 보내면 둘째 요청(잠금)이 옛 메모 빈칸을 실어 첫째 수정(메모)을 지운다
    let stored = line("a");
    const bodies: ChartLineEditBody[] = [];
    server.use(
      http.put(`${API}/lines/a`, async ({ request }) => {
        const edit = (await request.json()) as ChartLineEditBody;
        bodies.push(edit);
        await delay(30);
        stored = { ...stored, ...edit, levels: edit.levels ?? null, updated_at: `t${bodies.length}` };
        return HttpResponse.json(stored);
      }),
    );
    const { client, wrapper } = setup([stored]);
    const { result } = renderHook(() => useChartDrawing(KOSPI, "week"), { wrapper });

    let pending: Promise<unknown>[] = [];
    act(() => {
      pending = [
        result.current.edit(line("a"), { memo: "지지" }),
        result.current.edit(line("a"), { locked: true }),
      ];
    });
    // 응답 전에도 체크 칸·메모는 바로 바뀐다
    await waitFor(() =>
      expect(client.getQueryData<ChartLinesBody>(chartLinesQuery(KOSPI).queryKey)?.lines[0]).toMatchObject({
        memo: "지지",
        locked: true,
      }),
    );
    expect(bodies.length).toBeLessThan(2);
    await act(async () => {
      await Promise.all(pending);
    });

    // 두 요청은 차례대로 갔고 마지막 본문은 두 수정을 모두 싣는다(앞 본문이 뒤 수정을 미리 실어도 같은 뜻이다)
    expect(bodies).toHaveLength(2);
    expect(bodies.map((body) => body.memo)).toEqual(["지지", "지지"]);
    expect(bodies[1].locked).toBe(true);
    const cached = client.getQueryData<ChartLinesBody>(chartLinesQuery(KOSPI).queryKey);
    expect(cached?.lines[0]).toMatchObject({ memo: "지지", locked: true });
  });

  it("counts each queued nudge from the price the previous one saved", async () => {
    let stored = line("a");
    server.use(
      http.put(`${API}/lines/a`, async ({ request }) => {
        const edit = (await request.json()) as ChartLineEditBody;
        stored = { ...stored, anchors: edit.anchors };
        return HttpResponse.json(stored);
      }),
    );
    const { wrapper } = setup([stored]);
    const { result } = renderHook(() => useChartDrawing(KOSPI, "week"), { wrapper });
    const up = (latest: ChartLineBody) => ({
      anchors: latest.anchors.map((anchor) => ({ ...anchor, price: anchor.price + 1 })),
    });

    await act(async () => {
      await Promise.all([result.current.edit(line("a"), up), result.current.edit(line("a"), up)]);
    });

    expect(stored.anchors[0].price).toBe(2_502);
  });

  it("releases a drag even when saving it fails", async () => {
    server.use(
      http.put(`${API}/lines/a`, () =>
        HttpResponse.json(
          { detail: { code: "chart.line.anchor_order", message: "anchors must be in bar order" } },
          { status: 422 },
        ),
      ),
    );
    const { wrapper } = setup([line("a")]);
    const { result } = renderHook(() => useChartDrawing(KOSPI, "week"), { wrapper });

    act(() => result.current.startDrag(line("a"), 0));
    act(() => result.current.moveDrag({ bar_date: "2025-03-10", price: 2_600 }));
    act(() => result.current.endDrag());
    expect(result.current.drag?.committing).toBe(true);

    await waitFor(() => expect(result.current.drag).toBeNull());
    expect(result.current.saveError).not.toBeNull();
  });
});
