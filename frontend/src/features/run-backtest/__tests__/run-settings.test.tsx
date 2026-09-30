import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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
  initialRunEnvironmentValues,
  runEnvironmentFields,
  validateRunEnvironment,
  type RunEnvironmentValidation,
} from "../model/run-environment";
import {
  buildBacktestRunOptions,
  DEFAULT_BACKTEST_RUN_SETTINGS,
} from "../model/run-settings";
import { runFieldLabel } from "../model/run-settings-problems";
import {
  RUN_ENVIRONMENT_STORAGE_PREFIX,
  useBacktestRunSettings,
} from "../model/use-backtest-run-settings";
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

/**
 * 기본값에 기간·유니버스를 채운 패널이 싣는 실행 설정. 가격 충격 계수·직접 입력 세율은 고정 bp·법정 세율에서
 * 읽히지 않아(스키마 `x-applicable-when`, #352) 싣지 않는다.
 */
const ENVIRONMENT: RunEnvironment = {
  market: "KRX",
  frequency: "daily",
  start: "2021-01-01",
  end: "2026-08-31",
  universe_id: "krx.common-stock",
  timing: "next_open",
  participation_rate: 0.1,
  participation_basis: "session_volume",
  fee_bps: 15,
  slippage_bps: 10,
  impact_model: "fixed_bps",
  sell_tax: "krx_statutory",
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

const Harness = ({
  storageKey = "strategy-1",
  request = null,
}: {
  storageKey?: string;
  request?: BacktestRunSpec | null;
}) => {
  const controller = useBacktestRunSettings(storageKey);
  return (
    <>
      <BacktestRunSettings controller={controller} request={request} />
      <RunEnvironmentSummary controller={controller} />
      <output data-testid="request">
        {JSON.stringify(controller.requestOptions)}
      </output>
      <output data-testid="blocked">{controller.blockedReason}</output>
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

  // #266 리뷰 P2-1: OOS 시작일을 덜 치면 칸 값은 빈 문자열이다. 비운 것과 구분하지 않으면 OOS 구간 없이
  // 실행돼 사용자가 원한 표본 밖 측정이 아무 표시 없이 사라진다. 비운 칸은 그대로 통과한다(선택 칸).
  it("blocks a half-typed OOS start instead of silently running without the OOS window", () => {
    expect(
      buildBacktestRunOptions(DEFAULT_BACKTEST_RUN_SETTINGS, VALID, true),
    ).toEqual({ valid: false, options: null, errors: ["oos_incomplete"] });
    expect(
      buildBacktestRunOptions(DEFAULT_BACKTEST_RUN_SETTINGS, VALID).options
        ?.metric_windows,
    ).toEqual([]);
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

  // 이슈 #260: 0 이하는 backend(`BacktestRunSpec.__post_init__`)가 거절한다. 패널이 먼저 막지 않으면 배지는
  // "준비됨"인데 시작이 422 로 거절되고, 사용자는 어느 칸이 문제인지 모른다.
  it.each(["0", "-1"])(
    "blocks non-positive initial cash and annualization days %s before the request",
    (value) => {
      expect(
        buildBacktestRunOptions(
          {
            core: "rust",
            initialCashKrw: value,
            benchmarkSecurityId: "",
            annualizationDays: value,
            oosStart: "",
          },
          VALID,
        ),
      ).toEqual({
        valid: false,
        options: null,
        errors: ["initial_cash", "annualization_days"],
      });
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

  // #260: 서버 거절의 `field`(본문 점 경로)를 패널 칸 이름으로 바꾼다. 모르는 경로는 이름을 지어내지 않는다.
  it("names the panel field a coded start rejection points at", () => {
    const fields = runEnvironmentFields(RUN_ENVIRONMENT_SCHEMA);
    expect(runFieldLabel(fields, "initial_cash")).toBe("초기 자본");
    expect(runFieldLabel(fields, "annualization_days")).toBe("연환산 거래일");
    expect(runFieldLabel(fields, "environment.fee_bps")).toBe("수수료");
    expect(runFieldLabel(fields, "environment.end")).toBe("종료일");
    expect(runFieldLabel(fields, "metric_windows.0.start")).toBe("OOS 시작일");
    expect(runFieldLabel(fields, "core")).toBe("실행 core");
    expect(runFieldLabel(fields, "strategy_source")).toBeNull();
    expect(runFieldLabel(fields, "environment.unknown")).toBeNull();
  });

  // #266 리뷰 P3-1: 덜 친 날짜 칸은 값이 빈 문자열이다. 검증이 이를 "비었다"로만 보면 칸 아래는 "끝까지
  // 치라"고 하는데 요약 띠·차단 문장은 "채우라"고 해 같은 칸을 두고 두 원인을 말한다.
  it("reports a half-typed date as a date error, not a missing value", () => {
    const fields = runEnvironmentFields(RUN_ENVIRONMENT_SCHEMA);
    const values = { start: "", end: "", universe_id: "" };
    expect(validateRunEnvironment(fields, values).errors.start).toBe(
      "required",
    );
    expect(
      validateRunEnvironment(fields, values, new Set(["start"])).errors,
    ).toMatchObject({ start: "date", end: "required" });
  });

  // #352 C-P2-2(#343 V2-07): 모드에 따라 읽히는 칸은 스키마 `x-applicable-when` 에서 읽는다. 읽히는 칸을
  // 비우면 세 조건 모두 "준비됨"이 아니라 그 칸 오류이고, 읽히지 않는 칸은 값이 남아도 막지도 싣지도 않는다.
  it("requires a mode-dependent field only in the mode that reads it and leaves it out otherwise", () => {
    const fields = runEnvironmentFields(RUN_ENVIRONMENT_SCHEMA);
    const filled = initialRunEnvironmentValues(fields, {
      start: "2021-01-01",
      end: "2026-08-31",
      universe_id: "krx.common-stock",
    });
    const check = (values: Record<string, string>) =>
      validateRunEnvironment(fields, { ...filled, ...values });

    expect(check({ sell_tax: "custom", sell_tax_bps: "" }).errors).toEqual({
      sell_tax_bps: "required",
    });
    expect(
      check({ impact_model: "sqrt", impact_coefficient: "" }).errors,
    ).toEqual({ impact_coefficient: "required" });
    expect(
      check({ impact_model: "fixed_bps", slippage_bps: "" }).errors,
    ).toEqual({ slippage_bps: "required" });

    // 법정 세율에 남은 세율 값(마지막 사용값 등)은 읽히지 않아 싣지 않는다 — 서버도 그대로 받는다.
    const statutory = check({ sell_tax: "krx_statutory", sell_tax_bps: "12" });
    expect(statutory.valid).toBe(true);
    expect(statutory.environment).not.toHaveProperty("sell_tax_bps");
    expect(
      check({ sell_tax: "custom", sell_tax_bps: "12" }).environment,
    ).toMatchObject({ sell_tax: "custom", sell_tax_bps: 12 });
    const sqrt = check({ impact_model: "sqrt", slippage_bps: "" });
    expect(sqrt.environment).toMatchObject({
      impact_model: "sqrt",
      impact_coefficient: 1,
    });
    expect(sqrt.environment).not.toHaveProperty("slippage_bps");
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
    // 기간·유니버스는 스키마에 기본값이 없다 — 패널도 지어내지 않는다(P3-02 결정 1). 스키마 기본값이
    // null 인 선택 칸(직접 입력 거래세율)도 빈 칸으로 시작한다.
    expect(
      fields
        .filter((field) => field.defaultValue === null)
        .map((field) => field.name),
    ).toEqual(
      Object.entries(properties)
        .filter(([, node]) => (node as Record<string, unknown>).default == null)
        .map(([name]) => name),
    );
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
    // 차단 안내와 요약 띠가 "어느 칸을 채우라"를 칸 이름으로 말한다(P3-02 결정 1 보충).
    expect(
      screen.getByRole("region", { name: "실행 설정 요약" }),
    ).toHaveTextContent("실행 설정에서 시작일·종료일·유니버스 칸을 채우세요.");
    expect(screen.getByTestId("blocked")).toHaveTextContent(
      "실행 설정에서 시작일·종료일·유니버스 칸을 채우세요.",
    );
  });

  it("opens the panel at the first empty field from the summary band", async () => {
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    const band = await screen.findByRole("region", { name: "실행 설정 요약" });
    const fill = await screen.findByRole("button", {
      name: "실행 설정 채우기",
    });
    expect(band).toContainElement(fill);

    await user.click(fill);
    const start = screen.getByLabelText(/^시작일/);
    expect(start).toBeVisible();
    expect(start).toHaveFocus();

    // 채운 칸은 건너뛰고 다음 빈 칸으로 간다. 남은 칸만 차단 안내에 남는다.
    await user.type(start, "2021-01-01");
    await user.click(screen.getByRole("button", { name: "실행 설정 채우기" }));
    expect(screen.getByLabelText(/^종료일/)).toHaveFocus();
    expect(screen.getByTestId("blocked")).toHaveTextContent(
      "실행 설정에서 종료일·유니버스 칸을 채우세요.",
    );

    await user.type(screen.getByLabelText(/^종료일/), "2026-08-31");
    await user.type(
      screen.getByRole("textbox", { name: "유니버스" }),
      "krx.common-stock",
    );
    expect(
      screen.queryByRole("button", { name: "실행 설정 채우기" }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("blocked")).toBeEmptyDOMElement();
  });

  // DEFECT-242-01: 기간·유니버스를 정한 뒤 다른 칸의 값이 틀리면, 차단 문장이 그 칸 이름과 이유를 말하고
  // 요약 띠의 버튼이 그 칸으로 초점을 옮긴다. "기간과 유니버스를 정하세요" 같은 고정 문장이 나오면 안 된다.
  it("names the wrong field and its reason when a filled environment has an out-of-range fee", async () => {
    renderWithQuery(<Harness />);
    const user = await openSettings();
    await fillPeriodAndUniverse(user);
    const fee = screen.getByRole("spinbutton", { name: /수수료/ });
    await user.clear(fee);
    await user.type(fee, "-1");
    await user.click(screen.getByLabelText("실행 설정 열기"));

    const reason = "실행 설정의 수수료 칸을 고치세요: 0bp 이상이어야 합니다.";
    expect(screen.getByTestId("blocked")).toHaveTextContent(reason);
    const band = screen.getByRole("region", { name: "실행 설정 요약" });
    expect(band).toHaveTextContent(reason);
    expect(band).not.toHaveTextContent("시작일·종료일·유니버스");

    await user.click(
      within(band).getByRole("button", { name: "실행 설정 고치기" }),
    );
    expect(screen.getByRole("spinbutton", { name: /수수료/ })).toHaveFocus();
  });

  it("points at the end date when the period is reversed and counts the other wrong fields", async () => {
    renderWithQuery(<Harness />);
    const user = await openSettings();
    await fillPeriodAndUniverse(user);
    const end = screen.getByLabelText(/^종료일/);
    await user.clear(end);
    await user.type(end, "2020-12-31");
    expect(screen.getByTestId("blocked")).toHaveTextContent(
      "실행 설정의 종료일 칸을 고치세요: 종료일은 시작일과 같거나 그 뒤여야 합니다.",
    );

    // 여러 칸이 틀리면 스키마 순서의 첫 칸을 말하고 나머지 개수를 붙인다.
    const slippage = screen.getByRole("spinbutton", { name: /슬리피지/ });
    await user.clear(slippage);
    await user.type(slippage, "-5");
    expect(screen.getByTestId("blocked")).toHaveTextContent(
      "실행 설정의 종료일 칸을 고치세요: 종료일은 시작일과 같거나 그 뒤여야 합니다. 이 밖에 1칸이 더 맞지 않습니다.",
    );
    await user.click(screen.getByLabelText("실행 설정 열기"));
    await user.click(screen.getByRole("button", { name: "실행 설정 고치기" }));
    expect(screen.getByLabelText(/^종료일/)).toHaveFocus();
  });

  // #266 재리뷰 P3-2: OOS 오류의 "고치기" 대상은 오류 코드가 아니라 OOS 칸(`oos_start`)이다. 대응 표를
  // 되돌리면 초점이 어디로도 가지 않는다.
  it("moves focus to the OOS start field when it is outside the run period", async () => {
    renderWithQuery(<Harness />);
    const user = await openSettings();
    await fillPeriodAndUniverse(user);
    await user.type(screen.getByLabelText(/^OOS 시작일/), "2020-12-31");
    await user.click(screen.getByLabelText("실행 설정 열기"));

    const band = screen.getByRole("region", { name: "실행 설정 요약" });
    expect(band).toHaveTextContent(
      "실행 설정의 OOS 시작일 칸을 고치세요: 실행 기간 안의 날짜여야 합니다.",
    );
    await user.click(
      within(band).getByRole("button", { name: "실행 설정 고치기" }),
    );
    expect(screen.getByLabelText(/^OOS 시작일/)).toHaveFocus();
  });

  it("names a run option field when only the run options are wrong", async () => {
    renderWithQuery(<Harness />);
    const user = await openSettings();
    await fillPeriodAndUniverse(user);
    const cash = screen.getByRole("spinbutton", { name: "초기 자본 (KRW)" });
    await user.clear(cash);
    await user.type(cash, "0");
    await user.click(screen.getByLabelText("실행 설정 열기"));

    // 실행 옵션 칸도 실행 설정 칸과 같은 문장 틀로 칸 이름과 이유를 말한다(이슈 #260).
    expect(screen.getByTestId("blocked")).toHaveTextContent(
      "실행 설정의 초기 자본 칸을 고치세요: 0보다 큰 숫자를 입력하세요.",
    );
    expect(requestBody()).toBeNull();
    const band = screen.getByRole("region", { name: "실행 설정 요약" });
    await user.click(
      within(band).getByRole("button", { name: "실행 설정 고치기" }),
    );
    expect(
      screen.getByRole("spinbutton", { name: "초기 자본 (KRW)" }),
    ).toHaveFocus();
  });

  // #352 C-P2-2: 패널은 지금 모드에서 읽히지 않는 칸을 끄고, 읽히는 칸이 비면 그 칸 이름으로 막는다.
  it("turns the sell tax rate on only for a custom rate and requires it there", async () => {
    renderWithQuery(<Harness />);
    const user = await openSettings();
    await fillPeriodAndUniverse(user);
    const rate = screen.getByRole("spinbutton", { name: "매도 거래세율 (bp)" });
    const method = screen.getByRole("combobox", { name: "매도 거래세" });
    expect(rate).toBeDisabled();
    expect(requestBody()?.environment).toEqual(ENVIRONMENT);

    await user.selectOptions(method, "custom");
    expect(rate).toBeEnabled();
    expect(screen.getByText("입력 확인")).toBeInTheDocument();
    expect(screen.getByTestId("blocked")).toHaveTextContent(
      "실행 설정에서 매도 거래세율 칸을 채우세요.",
    );
    expect(requestBody()).toBeNull();
    await user.type(rate, "12");
    expect(requestBody()?.environment).toEqual({
      ...ENVIRONMENT,
      sell_tax: "custom",
      sell_tax_bps: 12,
    });

    // 방식만 되돌리면 세율 칸이 꺼지고 남은 12 는 싣지 않는다 — "준비됨"이면 서버도 받는다(#343 V2-07 경우 3).
    await user.selectOptions(method, "krx_statutory");
    expect(rate).toBeDisabled();
    expect(rate).toHaveValue(12);
    expect(screen.getByText("준비됨")).toBeInTheDocument();
    expect(requestBody()?.environment).toEqual(ENVIRONMENT);
  });

  it("reads the slippage only for fixed bp impact and the coefficient only for square-root impact", async () => {
    renderWithQuery(<Harness />);
    const user = await openSettings();
    await fillPeriodAndUniverse(user);
    const slippage = screen.getByRole("spinbutton", { name: "슬리피지 (bp)" });
    const coefficient = screen.getByRole("spinbutton", {
      name: "가격 충격 계수",
    });
    expect(slippage).toBeEnabled();
    expect(coefficient).toBeDisabled();

    await user.selectOptions(
      screen.getByRole("combobox", { name: "가격 충격 모델" }),
      "sqrt",
    );
    expect(coefficient).toBeEnabled();
    expect(slippage).toBeDisabled();
    expect(requestBody()?.environment).toMatchObject({
      impact_model: "sqrt",
      impact_coefficient: 1,
    });
    expect(requestBody()?.environment).not.toHaveProperty("slippage_bps");
    // 요약 띠도 요청에 실리는 칸만 보인다.
    const band = screen.getByRole("region", { name: "실행 설정 요약" });
    expect(within(band).getByText("가격 충격 계수")).toBeInTheDocument();
    expect(within(band).queryByText("슬리피지 (bp)")).toBeNull();
  });

  // #352 C-P2-4: 스키마를 읽기 전에 업그레이드 응답의 실행 설정을 채워도 요청 단위 기록으로 남아, 스키마가
  // 오면 그 값으로 칸이 선다. 칸 목록이 없다고 전략별 저장값·마지막 사용값을 빈 값으로 덮지 않는다.
  it("keeps an environment applied before the schema arrives and stores it, not an empty one", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get(`${API}/api/v1/run-environments/schema`, async () => {
        await gate;
        return HttpResponse.json({
          schema_hash: "h",
          schema: RUN_ENVIRONMENT_SCHEMA,
        });
      }),
    );
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    await user.click(
      screen.getByRole("button", { name: "apply upgraded environment" }),
    );
    const stored = (key: string): unknown =>
      JSON.parse(
        localStorage.getItem(`${RUN_ENVIRONMENT_STORAGE_PREFIX}:${key}`) ??
          "null",
      );
    expect(stored("strategy-1")).toMatchObject({
      start: "2021-01-01",
      universe_id: "krx.common-stock",
      fee_bps: "5",
    });
    expect(stored("last")).toEqual(stored("strategy-1"));
    expect(requestBody()).toBeNull();

    release();
    await waitFor(() =>
      expect(requestBody()?.environment).toEqual({
        ...ENVIRONMENT,
        fee_bps: 5,
      }),
    );
    await user.click(screen.getByLabelText("실행 설정 열기"));
    expect(screen.getByRole("spinbutton", { name: /수수료/ })).toHaveValue(5);
  });

  // DEFECT-242-04 (a): 단위는 스키마(`x-unit`·`x-display-unit`)에서 읽는다. 참여율은 %로 보이고 비율로 보낸다.
  it("shows the participation rate in the schema display unit and sends the ratio", async () => {
    renderWithQuery(<Harness />);
    const user = await openSettings();
    await fillPeriodAndUniverse(user);
    const participation = screen.getByRole("spinbutton", {
      name: "참여율 (%)",
    });
    expect(participation).toHaveValue(10);
    expect(screen.getByRole("spinbutton", { name: "수수료 (bp)" })).toHaveValue(
      15,
    );
    await user.clear(participation);
    await user.type(participation, "25");
    expect(requestBody()?.environment).toEqual({
      ...ENVIRONMENT,
      participation_rate: 0.25,
    });
    // 마지막 사용값은 요청 단위(비율)로 저장한다 — 표시 단위가 바뀌어도 저장값의 뜻은 그대로다.
    const stored = JSON.parse(
      localStorage.getItem(`${RUN_ENVIRONMENT_STORAGE_PREFIX}:strategy-1`) ??
        "{}",
    ) as Record<string, string>;
    expect(stored.participation_rate).toBe("0.25");
    // 친 문자열은 그대로 보인다 — 요청 단위(0.125)로 바꿨다 되돌려 "12.5" 로 다시 쓰지 않는다(#352 C-P2-4).
    fireEvent.change(participation, { target: { value: "12.50" } });
    expect((participation as HTMLInputElement).value).toBe("12.50");
    expect(requestBody()?.environment).toMatchObject({
      participation_rate: 0.125,
    });
    await user.clear(participation);
    await user.type(participation, "150");
    expect(screen.getByText("100% 이하여야 합니다.")).toBeInTheDocument();
    expect(requestBody()).toBeNull();
  });

  it.each([
    [
      "the storage accessor throws",
      () =>
        vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
          throw new DOMException("denied", "SecurityError");
        }),
    ],
    [
      "reads and writes throw",
      () => {
        vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
          throw new DOMException("denied", "SecurityError");
        });
        return vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
          throw new DOMException("full", "QuotaExceededError");
        });
      },
    ],
  ])("starts from schema defaults and still runs when %s", async (_, block) => {
    block();
    try {
      renderWithQuery(<Harness />);
      const user = await openSettings();
      expect(screen.getByRole("spinbutton", { name: /수수료/ })).toHaveValue(
        15,
      );
      expect(screen.getByLabelText(/^시작일/)).toHaveValue("");
      await fillPeriodAndUniverse(user);
      expect(requestBody()?.environment).toEqual(ENVIRONMENT);
    } finally {
      vi.restoreAllMocks();
    }
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
    expect(screen.getByText("3bp 이상이어야 합니다.")).toBeInTheDocument();
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
    expect(screen.getByText("0%보다 커야 합니다.")).toBeInTheDocument();
    expect(participation).toHaveAttribute("aria-invalid", "true");
    expect(requestBody()).toBeNull();
    await user.clear(participation);
    await user.type(participation, "20");

    const end = screen.getByLabelText(/^종료일/);
    await user.clear(end);
    await user.type(end, "2020-12-31");
    expect(
      screen.getByText("종료일은 시작일과 같거나 그 뒤여야 합니다."),
    ).toBeInTheDocument();
    expect(requestBody()).toBeNull();
  });

  // #264: 날짜 칸에 범위가 없으면 Chromium 이 연도를 6자리까지 받아, 이어 친 숫자가 연도로 빨려 들어가고
  // 칸이 빈다. 범위를 주면 연도가 4자리로 묶여 4자리 뒤에 월로 넘어간다(타이핑 경로는 US-DM-05 e2e).
  it("bounds every date field to four-digit years and names the range when a date falls outside it", async () => {
    renderWithQuery(<Harness />);
    const user = await openSettings();
    for (const date of [
      screen.getByLabelText(/^시작일/),
      screen.getByLabelText(/^종료일/),
      screen.getByLabelText(/^OOS 시작일/),
    ]) {
      expect(date).toHaveAttribute("min", "1900-01-01");
      expect(date).toHaveAttribute("max", "9999-12-31");
    }
    await fillPeriodAndUniverse(user);
    const start = screen.getByLabelText(/^시작일/);
    await user.clear(start);
    await user.type(start, "0021-01-01");
    expect(screen.getByTestId("blocked")).toHaveTextContent(
      "실행 설정의 시작일 칸을 고치세요: 1900-01-01부터 9999-12-31 사이의 날짜여야 합니다.",
    );
    expect(requestBody()).toBeNull();
  });

  // #270 P3-R2: 치는 도중(키를 뗄 때)의 덜 친 날짜는 실행만 막고, 칸 아래·요약 띠·오류 목록은 칸을 떠날 때 보인다.
  it("blocks the run while an OOS date is half typed but shows the error only once the field is left", async () => {
    renderWithQuery(<Harness />);
    const user = await openSettings();
    await fillPeriodAndUniverse(user);
    expect(requestBody()).not.toBeNull();
    const oos = screen.getByLabelText(/^OOS 시작일/);
    // jsdom 은 날짜 칸을 자리별로 채우지 않는다. 브라우저가 연도만 친 칸에 세우는 `badInput` 을 흉내 낸다.
    Object.defineProperty(oos, "validity", {
      configurable: true,
      value: { badInput: true },
    });
    const incomplete = "연·월·일까지 모두 올바르게 입력하세요.";

    fireEvent.keyUp(oos);
    expect(requestBody()).toBeNull();
    expect(oos).not.toHaveAttribute("aria-invalid");
    expect(screen.queryByText(incomplete, { exact: false })).toBeNull();

    fireEvent.blur(oos);
    expect(requestBody()).toBeNull();
    expect(oos).toHaveAttribute("aria-invalid", "true");
    expect(screen.getAllByText(incomplete, { exact: false })).not.toHaveLength(
      0,
    );
  });

  // #297 리뷰 P3-1: 다 친 날짜를 고쳐 치면 값이 빈 문자열로 바뀌며 change 가 난다. 그것도 칸 안에서 난 일이라
  // 실행만 막고, 오류는 칸을 떠날 때 선다.
  it("keeps the error hidden while a whole OOS date is being retyped", async () => {
    renderWithQuery(<Harness />);
    const user = await openSettings();
    await fillPeriodAndUniverse(user);
    const oos = screen.getByLabelText(/^OOS 시작일/);
    await user.type(oos, "2024-01-02");
    expect(requestBody()).not.toBeNull();
    // Backspace 한 번으로 한 자리가 빈 날짜 칸: 값은 빈 문자열, `badInput` 은 참이다.
    Object.defineProperty(oos, "validity", {
      configurable: true,
      value: { badInput: true },
    });
    fireEvent.change(oos, { target: { value: "" } });
    expect(requestBody()).toBeNull();
    expect(oos).not.toHaveAttribute("aria-invalid");
    expect(
      screen.queryByText("연·월·일까지 모두 올바르게 입력하세요.", {
        exact: false,
      }),
    ).toBeNull();

    fireEvent.blur(oos);
    expect(oos).toHaveAttribute("aria-invalid", "true");
  });

  // #297 재리뷰 P2-1: 실행 설정 날짜 칸은 필수라 빈 값 자체가 오류다. 칸 안에서 고쳐 치는 도중에도 "값을
  // 정하세요."가 아니라 날짜 문장이 서고, 요약 띠도 같은 원인을 말한다.
  it("names the date problem, not an empty field, while a start date is retyped in place", async () => {
    renderWithQuery(<Harness />);
    const user = await openSettings();
    await fillPeriodAndUniverse(user);
    const start = screen.getByLabelText(/^시작일/);
    // Backspace 한 번으로 한 자리가 빈 날짜 칸: 값은 빈 문자열, `badInput` 은 참이다.
    Object.defineProperty(start, "validity", {
      configurable: true,
      value: { badInput: true },
    });
    const date = "연·월·일을 모두 올바르게 입력하세요. 예: 2021-01-01";
    const blocked = `실행 설정의 시작일 칸을 고치세요: ${date}`;

    fireEvent.change(start, { target: { value: "" } });
    fireEvent.keyUp(start);
    expect(requestBody()).toBeNull();
    expect(start).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText(date)).toBeInTheDocument();
    expect(screen.queryByText("값을 정하세요.")).toBeNull();
    expect(screen.getByTestId("blocked")).toHaveTextContent(blocked);

    fireEvent.blur(start);
    expect(screen.getByText(date)).toBeInTheDocument();
    expect(screen.getByTestId("blocked")).toHaveTextContent(blocked);
  });

  it("says the date is incomplete when a remembered value is not a whole date", async () => {
    localStorage.setItem(
      `${RUN_ENVIRONMENT_STORAGE_PREFIX}:strategy-1`,
      JSON.stringify({ start: "2021-13", end: "2026-08-31" }),
    );
    renderWithQuery(<Harness />);
    await openSettings();
    expect(
      screen.getByText("연·월·일을 모두 올바르게 입력하세요. 예: 2021-01-01"),
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

  // #387 리뷰 P3-2: 아직 고치지 않은 패널의 첫 편집은 저장값 위에서 한다. 한 칸만 고쳐도 나머지 저장값이
  // 칸·요청·저장(전략별·마지막 사용값)에 남는다 — 빈 기록에서 시작하면 한 칸짜리 기록이 저장값을 덮는다.
  it("edits one field on top of the remembered environment and keeps the rest", async () => {
    const remembered = {
      start: "2021-01-01",
      end: "2026-08-31",
      universe_id: "krx.common-stock",
      participation_rate: "0.2",
    };
    localStorage.setItem(
      `${RUN_ENVIRONMENT_STORAGE_PREFIX}:strategy-1`,
      JSON.stringify(remembered),
    );
    renderWithQuery(<Harness />);
    const user = await openSettings();

    const fee = screen.getByRole("spinbutton", { name: /수수료/ });
    await user.clear(fee);
    await user.type(fee, "7");

    expect(requestBody()?.environment).toEqual({
      ...ENVIRONMENT,
      participation_rate: 0.2,
      fee_bps: 7,
    });
    const stored = (key: string): unknown =>
      JSON.parse(
        localStorage.getItem(`${RUN_ENVIRONMENT_STORAGE_PREFIX}:${key}`) ??
          "null",
      );
    expect(stored("strategy-1")).toEqual({ ...remembered, fee_bps: "7" });
    expect(stored("last")).toEqual({ ...remembered, fee_bps: "7" });
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

  // 검증 랩 V5-05: 새 시도인지는 backend 미리 계산이 판정하고, 패널은 실행 버튼이 보낼 요청 그대로 묻고 답만 옮긴다.
  it.each([
    [
      "new_trial",
      3,
      4,
      "결과가 나오면 새 시도로 셉니다. 계열 시도 수 3회 → 4회.",
    ],
    [
      "recheck",
      4,
      4,
      "이미 센 시도의 재확인이라 시도 수가 늘지 않습니다. 계열 시도 수 4회 그대로.",
    ],
    [
      "no_lineage",
      0,
      0,
      "저장한 적 없는 전략이라 이 실행은 시도 수에 들지 않습니다. 리비전을 저장한 뒤 실행하면 셉니다.",
    ],
  ] as const)(
    "shows the backend trial verdict %s for the exact run request once the panel opens",
    async (reason, count, after, sentence) => {
      const asked: unknown[] = [];
      server.use(
        http.post(
          `${API}/api/v1/backtests/trial-preview`,
          async ({ request }) => {
            asked.push(await request.json());
            return HttpResponse.json({
              lineage_id: reason === "no_lineage" ? null : "strategy-1",
              trial_key: "k",
              trial_count: count,
              new_trial: reason === "new_trial",
              trial_count_after: after,
              reason,
            });
          },
        ),
      );
      renderWithQuery(<Harness request={acceptedRequest} />);
      // 패널을 열기 전에는 묻지 않는다 — 스키마 응답이 그려질 때까지 기다린 뒤에도 0건이다.
      await screen.findByRole("button", { name: "실행 설정 채우기" });
      expect(asked).toHaveLength(0);
      await openSettings();

      expect(
        await screen.findByRole("status", { name: "시도 영향" }),
      ).toHaveTextContent(sentence);
      expect(asked).toEqual([acceptedRequest]);
    },
  );

  // 교정은 연구 구간 거절이 연구 하한을 실었을 때만 있다 — 다른 거절이나 날짜 없는 detail 에 버튼을 만들지 않는다.
  it("offers the start date fix only for a research window rejection that carries the research start", async () => {
    const { result } = renderHook(() => useBacktestRunSettings("strategy-1"), {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={new QueryClient()}>
          {children}
        </QueryClientProvider>
      ),
    });
    const researchStart = { research_start: "2020-01-02" };

    expect(
      result.current.rejectionFix(
        "backtest.run.research_window_violation",
        researchStart,
      )?.label,
    ).toBe("시작일을 2020-01-02로");
    expect(
      result.current.rejectionFix(
        "backtest.strategy.requires_upgrade",
        researchStart,
      ),
    ).toBeNull();
    expect(
      result.current.rejectionFix("backtest.run.research_window_violation", {}),
    ).toBeNull();
    expect(result.current.rejectionFix(null, researchStart)).toBeNull();
  });

  // 봉인 겹침처럼 미리 계산이 거절되면 줄을 그리지 않는다 — 같은 거절은 실행 버튼이 이유·교정과 함께 보인다.
  it("draws no trial line when the preview is rejected", async () => {
    let asked = 0;
    server.use(
      http.post(`${API}/api/v1/backtests/trial-preview`, () => {
        asked += 1;
        return HttpResponse.json(
          {
            detail: {
              code: "backtest.run.research_window_violation",
              message: "측정 시작일이 연구 구간 밖이라 실행할 수 없다",
              sealed_start: "2016-01-01",
              sealed_end: "2019-12-31",
              research_start: "2020-01-02",
            },
          },
          { status: 422 },
        );
      }),
    );
    renderWithQuery(<Harness request={acceptedRequest} />);
    await openSettings();

    await waitFor(() => expect(asked).toBe(1));
    expect(screen.queryByRole("status", { name: "시도 영향" })).toBeNull();
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
      "초기 자본: 0보다 큰 숫자를 입력하세요.",
    );
    expect(cash).toHaveAttribute("aria-invalid", "true");
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

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(
        "실행 제어 요청에 실패했습니다. 상태를 새로 확인한 뒤 다시 시도하세요.",
      );
      expect(within(alert).getByRole("group")).toHaveTextContent(
        "run was retired",
      );
      await waitFor(() => expect(unhandled).not.toHaveBeenCalled());
    } finally {
      window.removeEventListener("unhandledrejection", unhandled);
    }
  });

  // #268 리뷰 P3-3: 결과 화면의 재실행도 편집기 툴바와 같은 경로로 시작 거절을 말한다 — 코드의 번역을
  // 본문으로, 거절이 짚은 칸은 실행 설정 칸 이름으로, 서버 원문은 접힌 상세로.
  it("explains a coded rerun rejection like the editor toolbar", async () => {
    servedSchema = RUN_ENVIRONMENT_SCHEMA;
    server.use(
      schemaHandler,
      http.post(`${API}/api/v1/backtests`, () =>
        HttpResponse.json(
          {
            detail: {
              code: "backtest.run.field_invalid",
              field: "environment.fee_bps",
              message:
                "Value error, run environment value is out of range — field=environment.fee_bps",
            },
          },
          { status: 422 },
        ),
      ),
    );
    renderWithQuery(
      <BacktestRunActions
        runId="old-run"
        status="completed"
        request={acceptedRequest}
        onReplayed={vi.fn()}
      />,
    );
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "동일 설정 재실행" }));

    const alert = await screen.findByRole("alert");
    await waitFor(() =>
      expect(alert).toHaveTextContent(
        "동일 설정으로 다시 실행하지 못했습니다: 서버가 실행 설정의 수수료 칸 값을 받지 않았습니다.",
      ),
    );
    // 결과 화면에는 실행 설정 패널이 없다 — 문장이 고칠 곳을 말한다(#270 P3-R3).
    expect(alert).toHaveTextContent(
      "전략 편집기의 실행 설정에서 그 칸을 고친 뒤 다시 시작하세요.",
    );
    expect(alert).not.toHaveTextContent("API request failed");
    const reason = within(alert).getByRole("group");
    expect(reason).toHaveTextContent("서버 사유");
    expect(reason).toHaveTextContent("field=environment.fee_bps");
    expect(reason).not.toHaveAttribute("open");
  });

  // 검증 랩 V1-01: 연구 구간 거절 문장의 날짜는 번역에 적지 않고 detail 값으로 채운다(날짜 owner 는 backend).
  it("fills the research window dates from the rejection detail", async () => {
    server.use(
      http.post(`${API}/api/v1/backtests`, () =>
        HttpResponse.json(
          {
            detail: {
              code: "backtest.run.research_window_violation",
              message: "측정 시작일이 연구 구간 밖이라 실행할 수 없다 — got=start=2018-01-02",
              sealed_start: "2016-01-01",
              sealed_end: "2019-12-31",
              research_start: "2020-01-02",
            },
          },
          { status: 422 },
        ),
      ),
    );
    renderWithQuery(
      <BacktestRunActions
        runId="old-run"
        status="completed"
        request={acceptedRequest}
        onReplayed={vi.fn()}
      />,
    );
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "동일 설정 재실행" }));

    const alert = await screen.findByRole("alert");
    await waitFor(() =>
      expect(alert).toHaveTextContent(
        "시작일이 연구 구간 밖입니다. 2016-01-01~2019-12-31은 홀드아웃으로 봉인돼 있고 그 앞도 측정하지 않습니다. 전략 편집기의 실행 설정에서 시작일을 2020-01-02 이후로 옮긴 뒤 다시 시작하세요.",
      ),
    );
    expect(alert).not.toHaveTextContent("{");
  });

  // #304: 결과 화면에는 실행 설정 패널이 없다 — 칸을 짚지 않은 거절도 고칠 곳(전략 편집기)을 말한다.
  it("tells where to fix a rerun refused as an invalid request", async () => {
    server.use(
      http.post(`${API}/api/v1/backtests`, () =>
        HttpResponse.json(
          {
            detail: {
              code: "backtest.run.invalid",
              message:
                "oos start must fall inside the run window — got=2017-01-02",
            },
          },
          { status: 422 },
        ),
      ),
    );
    renderWithQuery(
      <BacktestRunActions
        runId="old-run"
        status="completed"
        request={acceptedRequest}
        onReplayed={vi.fn()}
      />,
    );
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "동일 설정 재실행" }));

    const alert = await screen.findByRole("alert");
    await waitFor(() =>
      expect(alert).toHaveTextContent(
        "동일 설정으로 다시 실행하지 못했습니다: 이 실행 요청은 시작할 수 없습니다. 서버 사유를 보고 전략 편집기에서 실행 설정(기간·OOS 시작일)이나 전략을 고치세요.",
      ),
    );
    expect(within(alert).getByRole("group")).toHaveTextContent(
      "oos start must fall inside the run window",
    );
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

      // 코드에 번역이 없으면 일반 문구를 본문으로 쓰고, 서버 원문은 접힌 서버 사유에만 둔다(#268 리뷰 P3-3).
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(
        "동일 설정으로 다시 실행하지 못했습니다: 서버가 실행 요청을 받지 않았습니다.",
      );
      expect(within(alert).getByRole("group")).toHaveTextContent(
        "engine unavailable",
      );
      expect(within(alert).getByRole("group")).not.toHaveAttribute("open");
      expect(onReplayed).not.toHaveBeenCalled();
      await waitFor(() => expect(unhandled).not.toHaveBeenCalled());
    } finally {
      window.removeEventListener("unhandledrejection", unhandled);
    }
  });
});
