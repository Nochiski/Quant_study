import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import type { ReactElement, ReactNode } from "react";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import type { BacktestRunSpec, RunEnvironment } from "../../../shared/api";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import {
  runEnvironmentFields,
  type RunEnvironmentValidation,
} from "../model/run-environment";
import {
  buildBacktestRunOptions,
  DEFAULT_BACKTEST_RUN_SETTINGS,
} from "../model/run-settings";
import { useBacktestRunSettings } from "../model/use-backtest-run-settings";
import { BacktestRunActions } from "../ui/backtest-run-actions";
import { BacktestRunSettings } from "../ui/backtest-run-settings";
import { RunEnvironmentSummary } from "../ui/run-environment-summary";

const API = "http://localhost:8000";
const acceptedRequest: BacktestRunSpec = {
  strategy_source: {
    kind: "saved_revision",
    strategy_id: "strategy-1",
    revision: 4,
    expected_spec_hash: "a".repeat(64),
  },
  core: "python",
  initial_cash: 123_456_789,
  benchmark_security_id: "sec-benchmark",
  annualization_days: 260,
  metric_windows: [
    {
      scope: "out_of_sample",
      start: "2025-01-02",
      end: "2026-08-31",
      label: "OOS 2025-01-02",
    },
  ],
};

let cancelledRun: string | null = null;
let replayedRequest: BacktestRunSpec | null = null;
const server = setupServer(
  http.post(`${API}/api/v1/backtests/:runId/cancel`, ({ params }) => {
    cancelledRun = String(params.runId);
    return HttpResponse.json({
      run_id: params.runId,
      status: "cancel_requested",
      progress: 0.4,
      stage: "cancellation",
      message: "Cancellation requested",
      created_at: "2026-09-06T00:00:00Z",
      updated_at: "2026-09-06T00:00:01Z",
    });
  }),
  http.post(`${API}/api/v1/backtests`, async ({ request }) => {
    replayedRequest = (await request.json()) as BacktestRunSpec;
    return HttpResponse.json(
      {
        run: {
          run_id: "run-replayed",
          status: "queued",
          progress: 0,
          stage: "queued",
          message: "Run accepted",
          created_at: "2026-09-06T00:00:02Z",
          updated_at: "2026-09-06T00:00:02Z",
        },
      },
      { status: 202 },
    );
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  cancelledRun = null;
  replayedRequest = null;
});
afterAll(() => server.close());

const renderWithQuery = (ui: ReactElement) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(ui, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
};

/** backend 가 만든 실행 설정 스키마 사본(`tools/export_runtime_schema.py`). */
const RUN_ENVIRONMENT_SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/run-environment-schema.json"),
) as Record<string, unknown>;

const ENVIRONMENT: RunEnvironment = {
  market: "KRX",
  frequency: "daily",
  start: "2021-01-01",
  end: "2026-08-31",
  universe_id: "krx.common-stock",
  timing: "next_open",
  participation_rate: 0.1,
  fee_bps: 15,
  slippage_bps: 10,
  missing: "drop",
};

const VALID: RunEnvironmentValidation = {
  valid: true,
  environment: ENVIRONMENT,
  errors: {},
};
const INCOMPLETE: RunEnvironmentValidation = {
  valid: false,
  environment: null,
  errors: { start: "required" },
};

let servedSchema: Record<string, unknown> = RUN_ENVIRONMENT_SCHEMA;
const schemaHandler = http.get(`${API}/api/v1/run-environments/schema`, () =>
  HttpResponse.json({ schema_hash: "h", schema: servedSchema }),
);

const Harness = ({ storageKey = "strategy-1" }: { storageKey?: string }) => {
  const controller = useBacktestRunSettings(storageKey);
  return (
    <>
      <BacktestRunSettings controller={controller} />
      <RunEnvironmentSummary controller={controller} />
      <output data-testid="request">
        {JSON.stringify(controller.requestOptions)}
      </output>
      <button
        type="button"
        onClick={() =>
          controller.applyEnvironment({ ...ENVIRONMENT, fee_bps: 5 })
        }
      >
        apply upgraded environment
      </button>
    </>
  );
};

const openSettings = async () => {
  const user = userEvent.setup();
  await user.click(screen.getByLabelText("실행 설정 열기"));
  await screen.findByRole("textbox", { name: "유니버스" });
  return user;
};

const requestBody = (): Record<string, unknown> | null =>
  JSON.parse(screen.getByTestId("request").textContent ?? "null") as Record<
    string,
    unknown
  > | null;

const fillPeriodAndUniverse = async (
  user: ReturnType<typeof userEvent.setup>,
) => {
  await user.type(screen.getByLabelText(/^시작일/), "2021-01-01");
  await user.type(screen.getByLabelText(/^종료일/), "2026-08-31");
  await user.type(
    screen.getByRole("textbox", { name: "유니버스" }),
    "krx.common-stock",
  );
};

describe("backtest run settings", () => {
  it("builds explicit RunSpec fields with the validated run environment", () => {
    const result = buildBacktestRunOptions(
      {
        core: "python",
        initialCashKrw: "123456789",
        benchmarkSecurityId: " sec-benchmark ",
        annualizationDays: "260",
        oosStart: "2025-01-02",
      },
      VALID,
    );

    expect(result).toEqual({
      valid: true,
      errors: [],
      options: {
        core: "python",
        initial_cash: 123_456_789,
        benchmark_security_id: "sec-benchmark",
        annualization_days: 260,
        environment: ENVIRONMENT,
        metric_windows: [
          {
            scope: "out_of_sample",
            start: "2025-01-02",
            end: "2026-08-31",
            label: "OOS 2025-01-02",
          },
        ],
      },
    });
    if (result.valid) {
      expect(result.options).not.toHaveProperty("strategy");
      expect(result.options).not.toHaveProperty("strategy_source");
    }
  });

  it("blocks unrepresentable values and an incomplete run environment", () => {
    expect(
      buildBacktestRunOptions(
        {
          core: "rust",
          initialCashKrw: "not-a-number",
          benchmarkSecurityId: "",
          annualizationDays: "252.5",
          oosStart: "2020-12-31",
        },
        INCOMPLETE,
      ),
    ).toEqual({
      valid: false,
      options: null,
      errors: ["initial_cash", "annualization_days", "environment"],
    });
  });

  it("measures the OOS window against the run period owned by the run settings", () => {
    const fields = { ...DEFAULT_BACKTEST_RUN_SETTINGS, oosStart: "2020-12-31" };
    expect(buildBacktestRunOptions(fields, VALID)).toEqual({
      valid: false,
      options: null,
      errors: ["oos_out_of_range"],
    });
    expect(
      buildBacktestRunOptions({ ...fields, oosStart: "2026-08-31" }, VALID)
        .options?.metric_windows,
    ).toEqual([
      {
        scope: "out_of_sample",
        start: "2026-08-31",
        end: "2026-08-31",
        label: "OOS 2026-08-31",
      },
    ]);
  });

  it("blocks an unsafe integer before Number conversion can mutate the wire value", () => {
    expect(
      buildBacktestRunOptions(
        {
          core: "rust",
          initialCashKrw: "100000000",
          benchmarkSecurityId: "",
          annualizationDays: "9007199254740993",
          oosStart: "",
        },
        VALID,
      ),
    ).toEqual({
      valid: false,
      options: null,
      errors: ["annualization_days"],
    });
  });

  it.each(["0", "-1"])(
    "forwards safe semantic boundary %s for the backend contract to decide",
    (annualizationDays) => {
      const result = buildBacktestRunOptions(
        {
          core: "rust",
          initialCashKrw: "0",
          benchmarkSecurityId: "",
          annualizationDays,
          oosStart: "",
        },
        VALID,
      );
      expect(result.valid).toBe(true);
      expect(result.options?.annualization_days).toBe(
        Number(annualizationDays),
      );
      expect(result.options?.initial_cash).toBe(0);
    },
  );

  it("sends no benchmark by default because the security ID vocabulary is adapter-owned", () => {
    // 이슈 #154: mock 은 `sec-005930-1`, 실데이터는 `005930:1` — 어느 어휘도 frontend 가 굽지 않는다.
    expect(DEFAULT_BACKTEST_RUN_SETTINGS.benchmarkSecurityId).toBe("");
    const result = buildBacktestRunOptions(
      DEFAULT_BACKTEST_RUN_SETTINGS,
      VALID,
    );
    expect(result.valid).toBe(true);
    expect(result.options?.benchmark_security_id).toBeNull();
  });
});

describe("run environment panel", () => {
  beforeEach(() => {
    servedSchema = RUN_ENVIRONMENT_SCHEMA;
    server.use(schemaHandler);
    localStorage.clear();
  });

  it("draws every field from the run environment schema in schema order", () => {
    const fields = runEnvironmentFields(RUN_ENVIRONMENT_SCHEMA);
    const properties = RUN_ENVIRONMENT_SCHEMA.properties as Record<
      string,
      unknown
    >;

    expect(fields.map((field) => field.name)).toEqual(Object.keys(properties));
    expect(
      fields.filter((field) => field.required).map((field) => field.name),
    ).toEqual(RUN_ENVIRONMENT_SCHEMA.required);
    // 기간·유니버스는 스키마에 기본값이 없다 — 패널도 지어내지 않는다(P3-02 결정 1).
    expect(
      fields
        .filter((field) => field.defaultValue === null)
        .map((field) => field.name),
    ).toEqual(RUN_ENVIRONMENT_SCHEMA.required);
  });

  it("starts from schema defaults, leaves period and universe empty and blocks the run", async () => {
    renderWithQuery(<Harness />);
    await openSettings();

    expect(screen.getByRole("spinbutton", { name: /수수료/ })).toHaveValue(15);
    expect(screen.getByRole("combobox", { name: /결측 처리/ })).toHaveValue(
      "drop",
    );
    expect(screen.getByLabelText(/^시작일/)).toHaveValue("");
    expect(screen.getByRole("textbox", { name: "유니버스" })).toHaveValue("");
    expect(screen.getAllByText("값을 정하세요.")).toHaveLength(3);
    expect(screen.getByText("입력 확인")).toBeInTheDocument();
    expect(requestBody()).toBeNull();
    expect(
      screen.getByRole("region", { name: "실행 설정 요약" }),
    ).toHaveTextContent("기간과 유니버스가 정해지지 않았습니다");
  });

  it("reads defaults and bounds from the endpoint instead of hand-written values", async () => {
    const properties = RUN_ENVIRONMENT_SCHEMA.properties as Record<
      string,
      Record<string, unknown>
    >;
    servedSchema = {
      ...RUN_ENVIRONMENT_SCHEMA,
      properties: {
        ...properties,
        fee_bps: { ...properties.fee_bps, default: 7, minimum: 3 },
      },
    };
    renderWithQuery(<Harness />);
    const user = await openSettings();

    const fee = screen.getByRole("spinbutton", { name: /수수료/ });
    expect(fee).toHaveValue(7);
    await user.clear(fee);
    await user.type(fee, "2");
    expect(screen.getByText("3 이상이어야 합니다.")).toBeInTheDocument();
  });

  it("sends the filled environment and reports schema bounds and date order beside each field", async () => {
    renderWithQuery(<Harness />);
    const user = await openSettings();
    await fillPeriodAndUniverse(user);

    expect(requestBody()?.environment).toEqual(ENVIRONMENT);
    expect(
      screen.getByRole("region", { name: "실행 설정 요약" }),
    ).toHaveTextContent("krx.common-stock");

    const participation = screen.getByRole("spinbutton", { name: /참여율/ });
    await user.clear(participation);
    await user.type(participation, "0");
    expect(screen.getByText("0보다 커야 합니다.")).toBeInTheDocument();
    expect(participation).toHaveAttribute("aria-invalid", "true");
    expect(requestBody()).toBeNull();
    await user.clear(participation);
    await user.type(participation, "0.2");

    const end = screen.getByLabelText(/^종료일/);
    await user.clear(end);
    await user.type(end, "2020-12-31");
    expect(
      screen.getByText("종료일은 시작일과 같거나 그 뒤여야 합니다."),
    ).toBeInTheDocument();
    expect(requestBody()).toBeNull();
  });

  it("remembers the last used environment per strategy and falls back to the last one used", async () => {
    const first = renderWithQuery(<Harness storageKey="strategy-1" />);
    const user = await openSettings();
    await fillPeriodAndUniverse(user);
    first.unmount();

    renderWithQuery(<Harness storageKey="strategy-1" />);
    await openSettings();
    expect(screen.getByRole("textbox", { name: "유니버스" })).toHaveValue(
      "krx.common-stock",
    );
    cleanup();

    // 저장한 새 전략처럼 칸이 없는 전략은 마지막 사용값을 이어받는다.
    renderWithQuery(<Harness storageKey="strategy-2" />);
    await openSettings();
    expect(screen.getByLabelText(/^시작일/)).toHaveValue("2021-01-01");
  });

  it("replaces every field when an upgraded document's environment is applied", async () => {
    renderWithQuery(<Harness />);
    const user = await openSettings();
    await user.click(
      screen.getByRole("button", { name: "apply upgraded environment" }),
    );

    expect(requestBody()?.environment).toEqual({ ...ENVIRONMENT, fee_bps: 5 });
    expect(screen.getByRole("spinbutton", { name: /수수료/ })).toHaveValue(5);
  });

  it("explains that an empty benchmark runs without one", async () => {
    renderWithQuery(<Harness />);
    await openSettings();
    const benchmark = screen.getByRole("textbox", { name: /벤치마크 종목 ID/ });
    expect(benchmark).toHaveValue("");
    expect(
      screen.getByText("비우면 벤치마크 없이 실행합니다.", { exact: false }),
    ).toBeInTheDocument();
  });

  it("exposes every run option through accessible controls and reports invalid input", async () => {
    renderWithQuery(<Harness />);
    const user = await openSettings();
    await fillPeriodAndUniverse(user);
    await user.selectOptions(
      screen.getByRole("combobox", { name: "실행 core" }),
      "python",
    );
    const cash = screen.getByRole("spinbutton", { name: "초기 자본 (KRW)" });
    await user.clear(cash);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "초기 자본을 숫자로 입력하세요. 허용 범위는 서버가 검증합니다.",
    );
    expect(screen.getByText("입력 확인")).toBeInTheDocument();
  });
});

describe("backtest run actions", () => {
  it("cancels a nonterminal run and reruns the exact server-owned request", async () => {
    const onReplayed = vi.fn();
    const view = renderWithQuery(
      <BacktestRunActions
        runId="run-active"
        status="running"
        request={acceptedRequest}
        onReplayed={onReplayed}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "실행 취소" }));
    await waitFor(() => expect(cancelledRun).toBe("run-active"));

    view.rerender(
      <BacktestRunActions
        runId="run-active"
        status="cancelled"
        request={acceptedRequest}
        onReplayed={onReplayed}
      />,
    );
    await user.click(screen.getByRole("button", { name: "동일 설정 재실행" }));

    await waitFor(() =>
      expect(onReplayed).toHaveBeenCalledWith("run-replayed"),
    );
    expect(replayedRequest).toEqual(acceptedRequest);
  });

  it("renders a typed cancel 404 without leaking an unhandled rejection", async () => {
    server.use(
      http.post(`${API}/api/v1/backtests/:runId/cancel`, () =>
        HttpResponse.json(
          {
            detail: {
              code: "backtest.run.not_found",
              message: "run was retired",
            },
          },
          { status: 404 },
        ),
      ),
    );
    const unhandled = vi.fn();
    window.addEventListener("unhandledrejection", unhandled);
    try {
      renderWithQuery(
        <BacktestRunActions
          runId="missing-run"
          status="running"
          request={acceptedRequest}
          onReplayed={vi.fn()}
        />,
      );
      await userEvent
        .setup()
        .click(screen.getByRole("button", { name: "실행 취소" }));

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "run was retired",
      );
      await waitFor(() => expect(unhandled).not.toHaveBeenCalled());
    } finally {
      window.removeEventListener("unhandledrejection", unhandled);
    }
  });

  it("keeps navigation unchanged when a rerun fails with a server error", async () => {
    server.use(
      http.post(`${API}/api/v1/backtests`, () =>
        HttpResponse.json(
          { detail: { code: "server.error", message: "engine unavailable" } },
          { status: 500 },
        ),
      ),
    );
    const onReplayed = vi.fn();
    const unhandled = vi.fn();
    window.addEventListener("unhandledrejection", unhandled);
    try {
      renderWithQuery(
        <BacktestRunActions
          runId="failed-run"
          status="failed"
          request={acceptedRequest}
          onReplayed={onReplayed}
        />,
      );
      await userEvent
        .setup()
        .click(screen.getByRole("button", { name: "동일 설정 재실행" }));

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "engine unavailable",
      );
      expect(onReplayed).not.toHaveBeenCalled();
      await waitFor(() => expect(unhandled).not.toHaveBeenCalled());
    } finally {
      window.removeEventListener("unhandledrejection", unhandled);
    }
  });
});
