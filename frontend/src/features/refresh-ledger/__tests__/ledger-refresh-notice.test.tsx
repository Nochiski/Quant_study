import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import type { LedgerRefreshBody } from "../../../shared/api";
import { LedgerRefreshNotice } from "..";

const PATH = "http://localhost:8000/api/v1/ledger-refresh";
const FINISHED = "2026-11-05T03:10:00Z";

const status = (overrides: Partial<LedgerRefreshBody>): LedgerRefreshBody => ({
  code: "idle",
  message: "서버에 있는 최신 데이터와 같아요.",
  can_start: true,
  refreshing: false,
  reason: null,
  failure: null,
  needs_check: false,
  remote: {
    state: "up_to_date",
    new_tables: 0,
    download_bytes: 0,
    in_build_window: false,
    checked_at: null,
  },
  progress: null,
  active_work: null,
  started_at: null,
  finished_at: null,
  ...overrides,
});

let current = status({});
const server = setupServer(http.get(PATH, () => HttpResponse.json(current)));
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());

describe("LedgerRefreshNotice", () => {
  it("announces a finished refresh once and reloads the screen once, even after new work starts", async () => {
    let reads = 0;
    const Other = () => {
      useQuery({ queryKey: ["other"], queryFn: () => ++reads });
      return null;
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <Other />
        <LedgerRefreshNotice />
      </QueryClientProvider>,
    );
    current = status({ code: "running", refreshing: true, can_start: false });
    await waitFor(() => expect(reads).toBe(1));
    const reload = () => act(() => client.refetchQueries({ queryKey: ["ledger-refresh"] }));
    await reload();
    expect(screen.queryByRole("status")).toBeNull();

    current = status({ code: "succeeded", message: "새 데이터를 앱에 반영했어요.", finished_at: FINISHED });
    await reload();
    expect(await screen.findByRole("status")).toHaveTextContent("새 데이터를 앱에 반영했어요.");
    await waitFor(() => expect(reads).toBe(2)); // 다른 조회를 한 번 다시 읽는다

    // 반영 뒤 계산을 시작하면 code 가 바뀌지만 같은 끝난 받기다 — 다시 알리거나 다시 읽지 않는다
    current = status({ code: "busy_work", can_start: false, finished_at: FINISHED });
    await reload();
    current = status({ code: "succeeded", message: "새 데이터를 앱에 반영했어요.", finished_at: FINISHED });
    await reload();
    expect(reads).toBe(2);
    await userEvent.setup().click(screen.getByRole("button", { name: "알림 닫기" }));
    await reload();
    expect(screen.queryByRole("status")).toBeNull();
  });
});
