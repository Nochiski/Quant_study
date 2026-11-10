import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, delay, http } from "msw";
import { setupServer } from "msw/node";
import type { KeyboardEvent, ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import {
  chartLinesQuery,
  type ChartLineBody,
  type ChartLineEditBody,
  type ChartLinesBody,
  type ChartTarget,
} from "../../../entities/chart";
import { useChartDrawing } from "..";
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

// 지우기 콜백(되돌리기 쌓기는 `features/remove-chart-line`이 본다) — 늘 받는다
const accept = () => true;

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
    const { result } = renderHook(() => useChartDrawing(KOSPI, "week", accept), { wrapper });

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
    const { result } = renderHook(() => useChartDrawing(KOSPI, "week", accept), { wrapper });
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
    const { result } = renderHook(() => useChartDrawing(KOSPI, "week", accept), { wrapper });

    act(() => result.current.startDrag(line("a"), 0));
    act(() => result.current.moveDrag({ bar_date: "2025-03-10", price: 2_600 }));
    act(() => result.current.endDrag());
    expect(result.current.drag?.committing).toBe(true);

    await waitFor(() => expect(result.current.drag).toBeNull());
    expect(result.current.saveError).not.toBeNull();
  });

  it("moves only the price when a horizontal line is dragged by its body", async () => {
    // 몸통 끌기는 포인터 봉의 날짜를 쓰지 않는다 — 선이 시작하는 봉(기준 봉 날짜)은 그대로다
    const bodies: ChartLineEditBody[] = [];
    server.use(
      http.put(`${API}/lines/a`, async ({ request }) => {
        const edit = (await request.json()) as ChartLineEditBody;
        bodies.push(edit);
        return HttpResponse.json({ ...line("a"), anchors: edit.anchors });
      }),
    );
    const { wrapper } = setup([line("a")]);
    const { result } = renderHook(() => useChartDrawing(KOSPI, "week", accept), { wrapper });

    act(() => result.current.startDrag(line("a"), "body"));
    act(() => result.current.moveDrag({ bar_date: "2025-06-02", price: 2_600 }));
    expect(result.current.drag?.anchors).toEqual([{ bar_date: line("a").anchors[0].bar_date, price: 2_600 }]);
    act(() => result.current.endDrag());

    await waitFor(() => expect(result.current.drag).toBeNull());
    expect(bodies.map((body) => body.anchors)).toEqual([[{ bar_date: line("a").anchors[0].bar_date, price: 2_600 }]]);
  });

  it("clears the selection only when a removal holding the selected line is accepted", () => {
    const { wrapper } = setup([line("a")]);
    let accepting = false;
    const { result } = renderHook(() => useChartDrawing(KOSPI, "week", () => accepting), { wrapper });
    act(() => result.current.dispatch({ type: "select", lineId: "a" }));

    // 앞 지우기가 나가 있어 받지 않았으면 고른 선을 그대로 둔다
    act(() => void result.current.remove([line("a")], "수평선"));
    expect(result.current.state.selectedId).toBe("a");

    accepting = true;
    // 고른 선이 없는 묶음을 지우면 고르기는 그대로다
    act(() => void result.current.remove([line("b"), line("c")], "수평 구간"));
    expect(result.current.state.selectedId).toBe("a");
    act(() => void result.current.remove([line("b"), line("a")], "수평 구간"));
    expect(result.current.state.selectedId).toBeNull();
  });

  it("removes the selected line of mine from a chart Delete key only when the chart itself has focus", () => {
    const { wrapper } = setup([line("a")]);
    const removed: string[] = [];
    const { result } = renderHook(
      () =>
        useChartDrawing(KOSPI, "week", (lines, title) => {
          removed.push(`${lines.map((item) => item.line_id).join(",")}:${title}`);
          return true;
        }),
      { wrapper },
    );
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const slider = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    const key = (values: Partial<KeyboardEvent<SVGSVGElement>>) =>
      ({ key: "Delete", target: svg, currentTarget: svg, preventDefault: () => undefined, ...values }) as
        KeyboardEvent<SVGSVGElement>;
    const auto = line("z", { source: "auto" });
    act(() => result.current.dispatch({ type: "select", lineId: "a" }));

    // 차트 안의 다른 초점(판정 구간 손잡이)·수정키 조합·다른 키는 받지 않는다
    expect(result.current.chartKey(key({ target: slider }), [line("a")])).toBe(false);
    expect(result.current.chartKey(key({ ctrlKey: true }), [line("a")])).toBe(false);
    expect(result.current.chartKey(key({ key: "ArrowLeft" }), [line("a")])).toBe(false);
    expect(removed).toEqual([]);

    let handled = false;
    act(() => {
      handled = result.current.chartKey(key({ key: "Backspace" }), [line("a")]);
    });
    expect(handled).toBe(true);
    expect(removed).toEqual([expect.stringMatching(/^a:수평선 /u)]);
    expect(result.current.state.selectedId).toBeNull();

    // 자동 선은 묶음 단위로 자동 후보 칸에서 지운다
    act(() => result.current.dispatch({ type: "select", lineId: "z" }));
    expect(result.current.chartKey(key({}), [auto])).toBe(false);
  });
});
