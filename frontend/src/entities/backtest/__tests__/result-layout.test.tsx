/**
 * 결과 화면 재배치(RD-07-A~E). 결론부터 읽는 순서, 수익 방향의 부호·낭독 문구, 두 축 카드의 정의와
 * 한계 문장, 월별 표의 접기, 그래프 요약 문장을 사용자가 보는 이름과 문구로 확인한다.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import type { BacktestRunResult, MetricDefinition } from "../../../shared/api";
import { BacktestRunDetail } from "../ui/backtest-run-detail";

afterEach(cleanup);

type MetricRow = [
  id: string,
  label: string,
  unit: MetricDefinition["unit"],
  category: MetricDefinition["category"],
  value: number | null,
];

const METRICS: MetricRow[] = [
  ["total_return", "Total return", "percent", "return", 0.384],
  ["benchmark_return", "Benchmark return", "percent", "benchmark", 0.263],
  ["excess_return", "Excess return", "percent", "benchmark", 0.121],
  ["sharpe", "Sharpe ratio", "ratio", "risk_adjusted", 0.92],
  ["sharpe_standard_error", "Sharpe standard error", "ratio", "risk_adjusted", 0.38],
  ["max_drawdown", "Maximum drawdown", "percent", "risk", -0.187],
  ["max_drawdown_recovery_sessions", "Drawdown recovery", "sessions", "risk", 143],
  ["turnover", "Turnover", "ratio", "exposure", 3.12],
  ["average_gross_exposure", "Average gross exposure", "percent", "exposure", 0.974],
  ["win_rate", "Win rate", "percent", "trade", 0.542],
  ["profit_factor", "Profit factor", "ratio", "trade", 1.48],
  ["trade_count", "Closed trades", "count", "trade", 412],
  ["total_fees", "Total fees", "currency", "cost", 4_120_000],
  ["total_taxes", "Total taxes", "currency", "cost", 6_180_000],
  ["total_slippage_cost", "Total slippage", "currency", "cost", 2_740_000],
];

const MONTHLY = [2020, 2021, 2022, 2023, 2024, 2025].flatMap((year) => [
  { year, month: 1, value: 0.021 },
  { year, month: 2, value: -0.013 },
]);

const result = (): BacktestRunResult => ({
  manifest: {
    run_id: "run-1",
    created_at: "2026-09-27T00:00:00Z",
    completed_at: "2026-09-27T00:00:05Z",
    engine_version: "test",
    run_fingerprint: "f".repeat(32),
    run_spec: {
      core: "rust",
      initial_cash: 100_000_000,
      annualization_days: 252,
      metric_windows: [],
      benchmark_security_id: "069500",
    },
    strategy_hash: "a".repeat(64),
    data_snapshot_id: "snapshot",
    target_tape_hash: "b".repeat(64),
    metric_registry_version: "metric-registry-v1",
    environment: {
      market: "KRX",
      frequency: "daily",
      start: "2020-01-02",
      end: "2025-12-30",
      universe_id: "krx.common-stock",
      timing: "next_open",
      participation_rate: 0.2,
      participation_basis: "adv20",
      fee_bps: 7,
      slippage_bps: 3,
      impact_model: "fixed_bps",
      impact_coefficient: 1,
      sell_tax: "krx_statutory",
      sell_tax_bps: null,
      missing: "zero",
    },
    environment_hash: "e".repeat(64),
    strategy_provenance: {
      kind: "inline_draft",
      spec_hash: "a".repeat(64),
      schema_version: "1.2",
    },
    warnings: [],
  },
  metric_definitions: METRICS.map(([metric_id, label, unit, category]) => ({
    metric_id,
    label,
    unit,
    category,
    higher_is_better: null,
    nullable: true,
  })),
  metrics: METRICS.map(([metric_id, , , , value]) => ({
    metric_id,
    value,
    scope: "full",
    sample_count: 1500,
  })),
  series: {
    equity: [
      { session: "2020-01-02", equity: 100_000_000, benchmark_equity: 100_000_000 },
      { session: "2022-10-12", equity: 90_000_000, benchmark_equity: 95_000_000 },
      { session: "2025-12-30", equity: 138_400_000, benchmark_equity: 126_300_000 },
    ],
    drawdown: [
      { session: "2020-01-02", drawdown: 0 },
      { session: "2022-10-12", drawdown: -0.187 },
      { session: "2025-12-30", drawdown: 0 },
    ],
    monthly_returns: MONTHLY,
    rolling_sharpe: [],
    rolling_sharpe_window_sessions: 126,
  },
  artifacts: {
    snapshots: [],
    positions: [],
    orders: [],
    fills: [],
    costs: [],
    trades: [],
  },
});

const ACCOUNT = {
  initial_cash: 100_000_000,
  final_equity: 138_400_000,
  final_cash: 1_000_000,
  pnl: 38_400_000,
  total_return: 0.384,
  first_snapshot_date: "2020-01-02",
  last_snapshot_date: "2025-12-30",
  snapshot_count: 1500,
};

describe("결과 화면 재배치", () => {
  it("결과 한눈에서 수익 방향을 부호·낭독 문구와 함께 보이고 곁들이는 값을 붙인다", () => {
    render(<BacktestRunDetail result={result()} account={ACCOUNT} />);
    const summary = screen.getByRole("region", { name: "결과 한눈에" });
    const card = (label: string) =>
      within(summary).getByText(label, { exact: true }).closest("div")!;

    expect(card("Total return")).toHaveTextContent("상승 +38.40%");
    expect(card("Total return")).toHaveTextContent("벤치마크 상승 +26.30%");
    expect(card("Excess return")).toHaveTextContent("상승 +12.10%");
    expect(card("Sharpe ratio")).toHaveTextContent("0.9200");
    expect(card("Sharpe ratio")).toHaveTextContent("오차 ±0.3800");
    expect(card("Maximum drawdown")).toHaveTextContent("하락 −18.70%");
    expect(card("Maximum drawdown")).toHaveTextContent("회복까지 143거래일");
    expect(card("최종 평가액")).toHaveTextContent("₩138,400,000");
    expect(card("최종 평가액")).toHaveTextContent("비용 반영 후 · 시작 ₩100,000,000");
  });

  it("계좌 요약이 없으면 최종 평가액 카드를 만들지 않는다", () => {
    render(<BacktestRunDetail result={result()} />);
    const summary = screen.getByRole("region", { name: "결과 한눈에" });
    expect(within(summary).queryByText("최종 평가액")).toBeNull();
  });

  it("결론 → 근거 → 그래프 → 두 축 → 월별 → 거래 → 용량 → 전체 지표 순서로 놓는다", () => {
    render(
      <BacktestRunDetail
        result={result()}
        validationReport={<p>검증 카드</p>}
        capacityCheck={<section aria-label="용량 확인 자리" />}
      />,
    );
    const order = [
      screen.getByRole("region", { name: "결과 한눈에" }),
      screen.getByRole("region", { name: "운일까, 튼튼할까" }),
      screen.getByRole("region", { name: "자산 곡선과 낙폭" }),
      screen.getByRole("region", { name: "종목 선정과 손익 구조" }),
      screen.getByRole("region", { name: "월별 수익률" }),
      screen.getByRole("heading", { name: "Closed trades" }),
      screen.getByRole("region", { name: "용량 확인 자리" }),
      screen.getByRole("region", { name: "Raw metric table" }),
    ];
    for (const [index, element] of order.slice(1).entries())
      expect(
        order[index]!.compareDocumentPosition(element) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    expect(
      screen.getByRole("region", { name: "운일까, 튼튼할까" }),
    ).toHaveTextContent(
      "앞으로의 수익을 보장하거나 확률로 약속하지 않아요",
    );
  });

  it("두 축 카드는 손익비 정의와 실현 통계 한계를 함께 보인다", () => {
    render(<BacktestRunDetail result={result()} regression={<p>회귀 자리</p>} />);
    const selection = screen.getByRole("region", { name: "종목 선정이 효과가 있었나" });
    const pnl = screen.getByRole("region", { name: "손익 구조는 어땠나" });

    expect(within(selection).getByText("회귀 자리")).toBeInTheDocument();
    expect(within(pnl).getByText("손익비").closest("div")).toHaveTextContent(
      "손익비profit factor · 이익 합 ÷ 손실 합1.4800",
    );
    expect(pnl).toHaveTextContent(
      "실제로 청산된 거래로 계산한 실현 통계예요. 지금 전략에는 손절·익절 규칙이 없어서 목표 손익비는 표시하지 않아요.",
    );
  });

  it("월별 수익률은 최근 세 해만 펼치고 이전 연도는 펼치기로 연다", async () => {
    const user = userEvent.setup();
    render(<BacktestRunDetail result={result()} />);
    const monthly = screen.getByRole("region", { name: "월별 수익률" });
    const recent = within(monthly).getByRole("table", { name: "월별 수익률 2023–2025" });

    expect(within(recent).getByRole("rowheader", { name: "2025" })).toBeVisible();
    expect(within(recent).queryByRole("rowheader", { name: "2022" })).toBeNull();
    expect(
      within(recent).getAllByRole("row")[1]!,
    ).toHaveTextContent("2023상승 +2.1%하락 −1.3%");

    const earlier = within(monthly).getByText("2020–2022 펼치기");
    expect(
      within(monthly).getByRole("table", { name: "월별 수익률 2020–2022", hidden: true }),
    ).not.toBeVisible();
    await user.click(earlier);
    expect(
      within(monthly).getByRole("table", { name: "월별 수익률 2020–2022" }),
    ).toBeVisible();
  });

  it("자산 곡선과 낙폭은 그래프마다 한 문장 요약과 그래프 밖 범례를 갖는다", () => {
    render(<BacktestRunDetail result={result()} />);
    const chart = screen.getByRole("region", { name: "자산 곡선과 낙폭" });

    expect(
      within(chart).getByRole("img", {
        name: "자산 곡선. 2020-01-02부터 2025-12-30까지 전략 평가액은 ₩100,000,000에서 ₩138,400,000로 움직였어요. 같은 기간 벤치마크는 ₩100,000,000에서 ₩126,300,000로 움직였어요.",
      }),
    ).toBeInTheDocument();
    expect(
      within(chart).getByRole("img", {
        name: "낙폭. 가장 깊이 내려간 때는 2022-10-12의 -18.7%예요.",
      }),
    ).toBeInTheDocument();
    expect(within(chart).getByText("전략")).toBeInTheDocument();
    expect(within(chart).getByText("벤치마크 069500")).toBeInTheDocument();
  });

  it("벤치마크 곡선이 없으면 범례와 요약에서 벤치마크를 빼고 그래프는 그린다", () => {
    const base = result();
    render(
      <BacktestRunDetail
        result={{
          ...base,
          series: {
            ...base.series,
            equity: base.series.equity.map((point) => ({
              ...point,
              benchmark_equity: null,
            })),
          },
        }}
      />,
    );
    const chart = screen.getByRole("region", { name: "자산 곡선과 낙폭" });
    expect(within(chart).queryByText(/벤치마크/u)).toBeNull();
    expect(
      within(chart).getByRole("img", { name: /^자산 곡선\. .*로 움직였어요\.$/u }),
    ).toBeInTheDocument();
  });
});
