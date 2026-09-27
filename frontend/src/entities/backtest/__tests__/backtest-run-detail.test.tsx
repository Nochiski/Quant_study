/**
 * run 상세가 실행된 실행 설정을 보인다(P3-02, Phase 2 감사 #16). schema 1.2 부터 기간·유니버스·비용·결측은
 * 전략 문서 밖에 있고 manifest 의 `environment` 가 그 실행 설정의 유일한 기록이다. 같은 전략을 다른
 * 기간으로 돌렸는지는 strategy hash 가 아니라 environment hash 로 갈린다.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import type { BacktestRunResult } from "../../../shared/api";
import { BacktestRunDetail } from "../ui/backtest-run-detail";

afterEach(cleanup);

const result = (): BacktestRunResult => ({
  manifest: {
    run_id: "run-1",
    created_at: "2026-09-27T00:00:00Z",
    completed_at: "2026-09-27T00:00:05Z",
    engine_core: "rust",
    engine_version: "test",
    run_fingerprint: "f".repeat(32),
    run_spec: { metric_windows: [] },
    strategy_hash: "a".repeat(64),
    data_snapshot_id: "snapshot",
    target_tape_hash: "b".repeat(64),
    metric_registry_version: "metric-registry-v1",
    initial_cash: 100_000_000,
    annualization_days: 252,
    fee_bps: 15,
    slippage_bps: 10,
    participation_rate: 0.1,
    environment: {
      market: "KRX",
      frequency: "daily",
      start: "2023-01-02",
      end: "2026-04-30",
      universe_id: "krx.common-stock",
      timing: "next_open",
      participation_rate: 0.2,
      fee_bps: 7,
      slippage_bps: 3,
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
  metric_definitions: [],
  metrics: [],
  series: { equity: [], drawdown: [], monthly_returns: [], rolling_sharpe: [] },
  artifacts: {
    snapshots: [],
    positions: [],
    orders: [],
    fills: [],
    costs: [],
    trades: [],
  },
});

describe("run 상세의 실행 설정", () => {
  it("manifest 의 실행 설정과 environment hash 를 보인다", async () => {
    render(<BacktestRunDetail result={result()} />);
    const user = userEvent.setup();
    const drawer = screen.getByRole("group", {
      name: "Manifest · 데이터 경고 · 재현성 정보",
    });
    await user.click(
      within(drawer).getByText("Manifest · 데이터 경고 · 재현성 정보"),
    );

    const row = (label: string) =>
      within(drawer).getByText(label, { exact: true }).closest("div")!;
    expect(row("실행 기간")).toHaveTextContent("2023-01-02 → 2026-04-30");
    expect(row("유니버스")).toHaveTextContent("krx.common-stock");
    expect(row("시장 · 빈도 · 체결")).toHaveTextContent(
      "KRX · daily · next_open",
    );
    // 평면 비용 필드(15bp)가 아니라 실행 설정이 실제로 쓴 값을 보인다(P3-02 결정 4).
    expect(row("비용 가정")).toHaveTextContent(
      "수수료 7bp · 슬리피지 3bp · 참여율 0.2",
    );
    expect(row("결측 처리")).toHaveTextContent("zero");
    expect(row("실행 설정 hash")).toHaveTextContent(`${"e".repeat(16)}…`);
  });
});
