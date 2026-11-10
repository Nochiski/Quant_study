import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, delay, http } from "msw";
import { setupServer } from "msw/node";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { chartLinesQuery, type ChartLineBody, type ChartLinesBody, type ChartTarget } from "../../../entities/chart";
import { useLineRemoval } from "..";
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

describe("useLineRemoval", () => {
  it("ignores a second delete while the first is in flight", async () => {
    let deletes = 0;
    server.use(
      http.delete(`${API}/lines/:id`, async () => {
        deletes += 1;
        await delay(30);
        return new HttpResponse(null, { status: 204 });
      }),
      http.get(`${API}/lines`, () => HttpResponse.json({ lines: [] }, { status: 200 })),
    );
    const { wrapper } = setup([line("a"), line("b")]);
    const { result } = renderHook(() => useLineRemoval(KOSPI), { wrapper });

    let accepted: boolean[] = [];
    act(() => {
      accepted = [result.current.removeLine([line("a")], "a")];
    });
    act(() => {
      accepted.push(result.current.removeLine([line("b")], "b"));
    });

    expect(accepted).toEqual([true, false]);
    await waitFor(() =>
      expect(result.current.notice).toEqual({ kind: "deleted", title: "a", lost: false, undoable: true }),
    );
    expect(deletes).toBe(1);
    expect(result.current.canUndo).toBe(true);
  });

  it("does not stack a line that cannot be drawn now, so undo never sends a doomed create", async () => {
    // 그릴 수 없는 선(issue)을 다시 만들면 서버가 기준점을 검사해 거절한다 — 쌓지 않고 알림이 바로 말한다
    let creates = 0;
    server.use(
      http.delete(`${API}/lines/:id`, () => new HttpResponse(null, { status: 204 })),
      http.post(`${API}/lines`, () => {
        creates += 1;
        return HttpResponse.json(line("new"), { status: 201 });
      }),
      http.get(`${API}/lines`, () => HttpResponse.json({ lines: [] })),
    );
    const broken = line("a", { issue: "anchor_outside_series" });
    const { wrapper } = setup([broken, line("b")]);
    const { result } = renderHook(() => useLineRemoval(KOSPI), { wrapper });

    act(() => {
      result.current.removeLine([broken], "a");
    });
    await waitFor(() =>
      expect(result.current.notice).toEqual({ kind: "deleted", title: "a", lost: true, undoable: false }),
    );
    expect(result.current.canUndo).toBe(false);
    expect(result.current.undo()).toBe(false);

    // 묶음에 섞이면 그릴 수 있는 선만 되살린다
    act(() => {
      result.current.removeLine([line("b"), line("c", { issue: "anchor_order" })], "b");
    });
    await waitFor(() =>
      expect(result.current.notice).toEqual({ kind: "deleted", title: "b", lost: true, undoable: true }),
    );
    act(() => {
      result.current.undo();
    });
    await waitFor(() => expect(result.current.notice).toEqual({ kind: "restored", title: "b" }));
    expect(creates).toBe(1);
  });

  it("forgets the undo stack and notice when another instrument opens", async () => {
    server.use(
      http.delete(`${API}/lines/:id`, () => new HttpResponse(null, { status: 204 })),
      http.get(`${API}/lines`, () => HttpResponse.json({ lines: [] })),
    );
    const { wrapper } = setup([line("a")]);
    const { result, rerender } = renderHook(({ target }) => useLineRemoval(target), {
      wrapper,
      initialProps: { target: KOSPI },
    });

    act(() => {
      result.current.removeLine([line("a")], "a");
    });
    await waitFor(() => expect(result.current.canUndo).toBe(true));

    rerender({ target: { kind: "stock", code: "practice:005930" } });

    expect(result.current.canUndo).toBe(false);
    expect(result.current.notice).toBeNull();
    expect(result.current.undo()).toBe(false);
  });

  it("does not stack a line whose delete answers after another instrument opened", async () => {
    // 지우기 응답 전에 종목을 바꾸면 앞 종목의 선이 새 종목의 되돌리기에 쌓이지 않는다
    let answered = false;
    server.use(
      http.delete(`${API}/lines/:id`, async () => {
        await delay(30);
        answered = true;
        return new HttpResponse(null, { status: 204 });
      }),
      http.get(`${API}/lines`, () => HttpResponse.json({ lines: [] })),
    );
    const { wrapper } = setup([line("a")]);
    const { result, rerender } = renderHook(({ target }) => useLineRemoval(target), {
      wrapper,
      initialProps: { target: KOSPI },
    });

    act(() => {
      result.current.removeLine([line("a")], "a");
    });
    rerender({ target: { kind: "stock", code: "practice:005930" } });
    await waitFor(() => expect(answered).toBe(true));
    await waitFor(() => expect(result.current.removing).toBe(false));

    expect(result.current.canUndo).toBe(false);
    expect(result.current.notice).toBeNull();
  });
});
