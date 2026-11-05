import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import type { LedgerRefreshBody } from "../../../shared/api";
import { LedgerRefreshControl } from "..";

const API = "http://localhost:8000";
const PATH = `${API}/api/v1/ledger-refresh`;

const status = (overrides: Partial<LedgerRefreshBody>): LedgerRefreshBody => ({
  code: "idle",
  message: "서버에 받을 새 데이터가 있어요.",
  can_start: true,
  refreshing: false,
  reason: null,
  failure: null,
  needs_check: false,
  remote: {
    state: "behind",
    new_tables: 3,
    download_bytes: 1_000_000,
    in_build_window: false,
    checked_at: "2026-11-05T03:00:00Z",
  },
  progress: null,
  active_work: { backtests: 0, experiments: 0, evaluations: 0 },
  started_at: null,
  finished_at: null,
  ...overrides,
});

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());

const renderControl = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <LedgerRefreshControl />
    </QueryClientProvider>,
  );

describe("LedgerRefreshControl", () => {
  it("draws nothing when the app runs on practice data", async () => {
    let reads = 0;
    server.use(
      http.get(PATH, () => {
        reads += 1;
        return HttpResponse.json(
          status({
            code: "unavailable",
            reason: "practice_data",
            can_start: false,
          }),
        );
      }),
    );
    const { container } = renderControl();
    await waitFor(() => expect(reads).toBe(1));
    expect(container).toBeEmptyDOMElement();
  });

  it("disables the button and says which work has to finish first", async () => {
    server.use(
      http.get(PATH, () =>
        HttpResponse.json(
          status({
            code: "busy_work",
            can_start: false,
            message: "진행 중인 백테스트 1건이 끝나야 받을 수 있어요.",
          }),
        ),
      ),
    );
    renderControl();
    const button = await screen.findByRole("button", { name: "DB 새로고침" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(
      "진행 중인 백테스트 1건이 끝나야 받을 수 있어요.",
    );
  });

  it("checks a stale remote once, confirms the download size and shows the steps after start", async () => {
    const user = userEvent.setup();
    let checks = 0;
    let started = 0;
    server.use(
      http.get(PATH, () => HttpResponse.json(status({ needs_check: true }))),
      http.post(`${PATH}/check`, () => {
        checks += 1;
        return HttpResponse.json(status({}));
      }),
      http.post(PATH, () => {
        started += 1;
        return HttpResponse.json(
          status({
            code: "running",
            can_start: false,
            refreshing: true,
            message: "데이터를 받는 중이에요.",
            started_at: "2026-11-05T03:00:00Z",
            progress: {
              stage: "download",
              stage_started_at: "2026-11-05T03:00:00Z",
              tables_total: 3,
              tables_done: 1,
              bytes_total: 1_000,
              bytes_done: 500,
            },
          }),
          { status: 202 },
        );
      }),
    );
    renderControl();
    await waitFor(() => expect(checks).toBe(1));
    expect(
      await screen.findByText("서버에 받을 새 데이터가 있어요"),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "DB 새로고침" }));
    const dialog = await screen.findByRole("dialog", {
      name: "새 데이터를 받을까요?",
    });
    expect(dialog).toHaveTextContent("받을 데이터 3종 · 약 1.0MB");
    await user.click(within(dialog).getByRole("button", { name: "받기 시작" }));

    await waitFor(() => expect(started).toBe(1));
    const running = await screen.findByRole("dialog", {
      name: "데이터를 받는 중이에요",
    });
    expect(within(running).getByText("받기")).toBeInTheDocument();
    expect(running).toHaveTextContent("1/3종");
    // 받기 단계는 받은 바이트 비율로 찬다
    expect(within(running).getByRole("progressbar")).toHaveAttribute(
      "value",
      "0.5",
    );

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "DB 새로고침" })).toHaveFocus();
  });
});
