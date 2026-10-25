import {
  QueryClient,
  QueryClientProvider,
  onlineManager,
  useQuery,
} from "@tanstack/react-query";
import {
  act,
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

import { ContinuousAccount } from "./continuous-account";

const API = "http://localhost:8000/api/v1";
const ID = "f82870dd-c054-4420-847f-335b7e678199";
const assumption =
  "학습 종료일 종가에 선택 가능했다고 가정하며 과거 실제 선택 시각의 증명이 아닙니다.";
const state = (status = "completed", evaluationId = ID) => ({
  evaluation_id: evaluationId,
  experiment_id: "experiment",
  status,
  progress: status === "running" ? 0.5 : 1,
  stage: status === "running" ? "engine" : status,
  created_at: "2026-10-17T00:00:00Z",
  error_code: status === "failed" ? "continuous.execution_failed" : null,
  error_detail: status === "failed" ? "합성 실행 실패 진단" : null,
  result:
    status !== "completed"
      ? null
      : {
          manifest: {
            evaluation_id: evaluationId,
            data_mode: "mock",
            selection_time_policy: "assumed_train_end_close",
          },
          summary: {
            first_snapshot_date: "2026-10-05",
            last_snapshot_date: "2026-10-17",
            snapshot_count: 10,
            initial_cash: 1000,
            final_cash: 41.3,
            final_equity: 1141.3,
            pnl: 141.3,
            total_return: 0.1413,
          },
          metric_definitions: [
            {
              metric_id: "max_drawdown",
              label: "Max drawdown",
              category: "risk",
              unit: "percent",
              precision: 2,
            },
          ],
          metrics: [
            {
              metric_id: "max_drawdown",
              scope: "full",
              value: -0.2,
              sample_count: 10,
            },
          ],
          artifacts: {
            fills: [
              {
                fill_id: "fill-1",
                session: "2026-10-17",
                security_id: "synthetic-a",
                side: "buy",
                quantity: "9",
                price: 100,
                fee: 9,
                sell_tax: 0,
              },
            ],
          },
          total_fees: 28.9,
          total_sell_tax: 19.8,
          warnings: [{ code: "synthetic", message: "합성 주의사항" }],
          interpretation: assumption,
        },
});
const latest = (evaluation: ReturnType<typeof state> | null = null) => ({
  readiness: { ready: true, code: null, detail: "" },
  evaluation,
  status: evaluation?.status ?? "not_started",
  data_mode: "mock",
  interpretation: assumption,
});
const server = setupServer(
  http.get(`${API}/experiments/experiment/continuous-account`, () =>
    HttpResponse.json(latest()),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  cleanup();
  server.resetHandlers();
  sessionStorage.clear();
  onlineManager.setOnline(true);
});
const mount = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ContinuousAccount experimentId="experiment" />
    </QueryClientProvider>,
  );
const section = () => screen.getByRole("region", { name: "연속 계좌 평가" });

describe("continuous account workflow", () => {
  it("uses server readiness and blocks unsupported data", async () => {
    server.use(
      http.get(`${API}/experiments/experiment/continuous-account`, () =>
        HttpResponse.json({
          ...latest(),
          readiness: {
            ready: false,
            code: "continuous.mock_only",
            detail: "자료 모드 거절",
          },
        }),
      ),
    );
    mount();
    expect(
      await screen.findByText(
        "현재 연속 계좌 평가는 모의 자료에서만 실행할 수 있습니다.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /연속 계좌 평가 실행|다시 평가/u }),
    ).toBeDisabled();
    await userEvent.setup().click(screen.getByText("평가 기준과 해석"));
    expect(screen.getByText(assumption)).toBeVisible();
  });

  it("starts once, reads live progress, and displays server account values", async () => {
    let release: (() => void) | undefined;
    let posts = 0;
    let detailReads = 0;
    let requestId = "";
    server.use(
      http.get(`${API}/experiments/experiment/continuous-account`, () =>
        HttpResponse.json(latest(state())),
      ),
      http.post(
        `${API}/experiments/experiment/continuous-account`,
        async ({ request }) => {
          posts += 1;
          requestId = ((await request.json()) as { client_request_id: string })
            .client_request_id;
          await new Promise<void>((resolve) => {
            release = resolve;
          });
          return HttpResponse.json(state("completed", requestId));
        },
      ),
      http.get(`${API}/continuous-accounts/:id`, ({ params }) => {
        detailReads += 1;
        return detailReads === 1
          ? HttpResponse.json({ detail: "not claimed" }, { status: 404 })
          : HttpResponse.json(state("running", String(params.id)));
      }),
    );
    const user = userEvent.setup();
    mount();
    await user.click(
      await screen.findByRole("button", {
        name: /연속 계좌 평가 실행|다시 평가/u,
      }),
    );
    expect(
      await screen.findByRole(
        "progressbar",
        { name: "계좌 평가 실행 중" },
        { timeout: 4_000 },
      ),
    ).toHaveAttribute("value", "0.5");
    expect(
      screen.queryByRole("button", { name: /연속 계좌 평가 실행|다시 평가/u }),
    ).not.toBeInTheDocument();
    expect(posts).toBe(1);
    expect(screen.queryByText("₩1,141.3")).not.toBeInTheDocument();
    expect(within(section()).getByText(requestId)).toBeInTheDocument();
    expect(detailReads).toBeGreaterThan(1);
    release?.();
    expect(await screen.findByText("계좌 평가 완료")).toBeInTheDocument();
    expect(within(section()).getByText("₩1,141.3")).toBeInTheDocument();
    expect(within(section()).getByText("₩41.3")).toBeInTheDocument();
    expect(within(section()).getByText("14.13%")).toBeInTheDocument();
    expect(
      screen.getByText("계좌 기록 기간 2026-10-05 ~ 2026-10-17 · 10개 기록"),
    ).toBeInTheDocument();
    await user.click(screen.getByText("평가 출처와 주의사항"));
    expect(screen.getByText("합성 주의사항")).toBeVisible();
    await user.click(screen.getByText("위험·성과 지표 전체 보기"));
    expect(screen.getByText("-20.00%")).toBeVisible();
    await user.click(screen.getByText("체결 내역 1건"));
    expect(screen.getByRole("cell", { name: "synthetic-a" })).toBeVisible();

    expect(posts).toBe(1);
  });

  it("recovers a lost POST by the same saved request instead of trusting an old latest result", async () => {
    let reads = 0;
    let posts = 0;
    let requestId = "";
    server.use(
      http.get(`${API}/experiments/experiment/continuous-account`, () =>
        HttpResponse.json(latest(state())),
      ),
      http.post(
        `${API}/experiments/experiment/continuous-account`,
        async ({ request }) => {
          posts += 1;
          requestId = ((await request.json()) as { client_request_id: string })
            .client_request_id;
          return HttpResponse.error();
        },
      ),
      http.get(`${API}/continuous-accounts/:id`, () => {
        reads += 1;
        return HttpResponse.json({ detail: "not claimed" }, { status: 404 });
      }),
    );
    const user = userEvent.setup();
    mount();
    await user.click(
      await screen.findByRole("button", {
        name: /연속 계좌 평가 실행|다시 평가/u,
      }),
    );
    expect(
      await screen.findByText(
        "아직 서버에서 이 요청을 확인하지 못했습니다. 새 평가를 만들지 않고 같은 요청의 상태를 확인합니다.",
      ),
    ).toBeInTheDocument();
    expect(posts).toBe(1);
    expect(
      screen.queryByRole("button", { name: /연속 계좌 평가 실행|다시 평가/u }),
    ).not.toBeInTheDocument();
    expect(requestId).not.toBe(ID);
    expect(screen.queryByText("₩1,141.3")).not.toBeInTheDocument();
    const originalRequestId = requestId;
    await user.click(
      screen.getByRole("button", { name: "같은 평가 요청 다시 보내기" }),
    );
    await waitFor(() => expect(posts).toBe(2));
    expect(requestId).toBe(originalRequestId);
    server.use(
      http.get(`${API}/continuous-accounts/:id`, ({ params }) =>
        HttpResponse.json(state("completed", String(params.id))),
      ),
    );
    await user.click(screen.getByRole("button", { name: "평가 상태 확인" }));
    expect(await screen.findByText("계좌 평가 완료")).toBeInTheDocument();
    expect(reads).toBeGreaterThan(0);
    expect(posts).toBe(2);
    const recoveredRequestId = requestId;
    cleanup();
    mount();
    expect(await screen.findByText("계좌 평가 완료")).toBeInTheDocument();
    expect(posts).toBe(2);
    const again = screen.getByRole("button", { name: "다시 평가" });
    await waitFor(() => expect(again).toBeEnabled());
    server.use(
      http.post(
        `${API}/experiments/experiment/continuous-account`,
        async ({ request }) => {
          posts += 1;
          requestId = ((await request.json()) as { client_request_id: string })
            .client_request_id;
          return HttpResponse.json(state("completed", requestId));
        },
      ),
    );
    await user.click(again);
    await waitFor(() => expect(posts).toBe(3));
    expect(requestId).not.toBe(recoveredRequestId);
  });

  it("does not show an old completed result as the result of a new failed evaluation", async () => {
    let requestId = "";
    server.use(
      http.get(`${API}/experiments/experiment/continuous-account`, () =>
        HttpResponse.json(latest(state())),
      ),
      http.post(
        `${API}/experiments/experiment/continuous-account`,
        async ({ request }) => {
          requestId = ((await request.json()) as { client_request_id: string })
            .client_request_id;
          return HttpResponse.json(state("failed", requestId));
        },
      ),
      http.get(`${API}/continuous-accounts/:id`, ({ params }) =>
        HttpResponse.json(state("failed", String(params.id))),
      ),
    );
    const user = userEvent.setup();
    mount();
    expect(await screen.findByText("₩1,141.3")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: /연속 계좌 평가 실행|다시 평가/u }),
    );
    expect(
      await screen.findByText(
        "계좌 평가 실행이 실패했습니다. 아래 사유를 확인하세요. 기존 실험 결과는 계속 볼 수 있습니다.",
      ),
    ).toBeInTheDocument();
    expect(requestId).not.toBe(ID);
    expect(screen.queryByText("₩1,141.3")).not.toBeInTheDocument();
    expect(screen.queryByText("계좌 평가 완료")).not.toBeInTheDocument();
  });

  it("does not refetch either Gone lookup on reconnect while a default query does reconnect", async () => {
    sessionStorage.setItem("continuous-account-request:experiment", ID);
    let latestReads = 0;
    let detailReads = 0;
    let controlReads = 0;
    server.use(
      http.get(`${API}/experiments/experiment/continuous-account`, () => {
        latestReads += 1;
        return HttpResponse.json(
          { detail: "permanent lookup diagnostic" },
          { status: 410 },
        );
      }),
      http.get(`${API}/continuous-accounts/:id`, () => {
        detailReads += 1;
        return HttpResponse.json(
          { detail: "permanent lookup diagnostic" },
          { status: 410 },
        );
      }),
    );
    const ReconnectControl = () => {
      useQuery({
        queryKey: ["reconnect-control"],
        queryFn: async () => ++controlReads,
      });
      return null;
    };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ContinuousAccount experimentId="experiment" />
        <ReconnectControl />
      </QueryClientProvider>,
    );
    expect(
      await screen.findByText(
        "이 평가를 다시 조회할 수 없습니다. 진단 사유를 확인하세요.",
      ),
    ).toBeInTheDocument();
    await waitFor(() => expect(controlReads).toBe(1));
    expect(latestReads).toBe(1);
    expect(detailReads).toBe(1);
    await act(async () => {
      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
    });
    // 양성 대조가 다시 실행된 뒤 검사하여 재연결 처리가 끝나기 전의 거짓 통과를 막는다.
    await waitFor(() => expect(controlReads).toBe(2));
    expect(latestReads).toBe(1);
    expect(detailReads).toBe(1);
    expect(
      screen.queryByRole("button", { name: "연속 계좌 평가 다시 불러오기" }),
    ).not.toBeInTheDocument();
  });

  it("separates a failed execution from a temporary lookup failure and never retries Gone", async () => {
    let status = 503;
    let reads = 0;
    server.use(
      http.get(`${API}/experiments/experiment/continuous-account`, () => {
        reads += 1;
        return status === 200
          ? HttpResponse.json(latest(state("failed")))
          : HttpResponse.json({ detail: "lookup diagnostic" }, { status });
      }),
    );
    const user = userEvent.setup();
    mount();
    expect(
      await screen.findByText(
        "평가 상태를 불러오지 못했습니다. 실행 여부를 확인하려면 상태를 다시 불러오세요.",
      ),
    ).toBeInTheDocument();
    expect(reads).toBe(1);
    status = 200;
    await user.click(
      screen.getByRole("button", { name: "연속 계좌 평가 다시 불러오기" }),
    );
    expect(
      await screen.findByText(
        "계좌 평가 실행이 실패했습니다. 아래 사유를 확인하세요. 기존 실험 결과는 계속 볼 수 있습니다.",
      ),
    ).toBeInTheDocument();
    cleanup();
    reads = 0;
    status = 410;
    mount();
    expect(
      await screen.findByText(
        "이 평가를 다시 조회할 수 없습니다. 진단 사유를 확인하세요.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "연속 계좌 평가 다시 불러오기" }),
    ).not.toBeInTheDocument();
    await waitFor(() => expect(reads).toBe(1));
  });
});
