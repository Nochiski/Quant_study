/**
 * run 상세가 실행된 실행 설정을 보인다(P3-02, Phase 2 감사 #16). schema 1.2 부터 기간·유니버스·비용·결측은
 * 전략 문서 밖에 있고 manifest 의 `environment` 가 그 실행 설정의 유일한 기록이다. 같은 전략을 다른
 * 기간으로 돌렸는지는 strategy hash 가 아니라 environment hash 로 갈린다.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import type { BacktestRunResult } from "../../../shared/api";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import { runEnvironmentFields } from "../model/run-environment-fields";
import { BacktestRunDetail } from "../ui/backtest-run-detail";

/** backend 가 만든 실행 설정 스키마 사본(`tools/export_runtime_schema.py`). */
const FIELDS = runEnvironmentFields(
  JSON.parse(
    readBackendFixture("strategy_documents/run-environment-schema.json"),
  ) as Record<string, unknown>,
);

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
  it("manifest 의 실행 설정을 스키마 칸 순서·이름·단위·값 이름으로 보이고 environment hash 를 보인다", async () => {
    render(<BacktestRunDetail result={result()} environmentFields={FIELDS} />);
    const user = userEvent.setup();
    const drawer = screen.getByRole("group", {
      name: "Manifest · 데이터 경고 · 재현성 정보",
    });
    await user.click(
      within(drawer).getByText("Manifest · 데이터 경고 · 재현성 정보"),
    );

    const row = (label: string) =>
      within(drawer).getByText(label, { exact: true }).closest("div")!;
    // 칸 목록은 손으로 적지 않고 실행 설정 스키마에서 읽는다(DEFECT-242-04). enum 은 패널과 같은 값 이름,
    // 숫자는 스키마 표시 단위(참여율 %)로 보인다.
    const labels = within(
      within(drawer).getByRole("group", { name: "실행 설정" }),
    )
      .getAllByRole("term")
      .map((term) => term.textContent);
    expect(labels).toEqual([
      "시장",
      "빈도",
      "시작일",
      "종료일",
      "유니버스",
      "체결 시점",
      "참여율 (%)",
      "수수료 (bp)",
      "슬리피지 (bp)",
      "매도 거래세",
      "매도 거래세율 (bp)",
      "결측 처리",
      "실행 설정 hash",
    ]);
    expect(row("시장")).toHaveTextContent("한국거래소(KRX)");
    expect(row("빈도")).toHaveTextContent("일봉");
    expect(row("시작일")).toHaveTextContent("2023-01-02");
    expect(row("유니버스")).toHaveTextContent("krx.common-stock");
    expect(row("체결 시점")).toHaveTextContent("다음 거래일 시가");
    // 평면 비용 필드(15bp)가 아니라 실행 설정이 실제로 쓴 값을 보인다(P3-02 결정 4).
    expect(row("참여율 (%)")).toHaveTextContent("20%");
    expect(row("수수료 (bp)")).toHaveTextContent("7bp");
    expect(row("매도 거래세")).toHaveTextContent("법정 세율(날짜별)");
    expect(row("매도 거래세율 (bp)")).toHaveTextContent("—");
    expect(row("결측 처리")).toHaveTextContent("0으로 채우기");
    expect(row("실행 설정 hash")).toHaveTextContent(`${"e".repeat(16)}…`);
  });

  it("스키마를 아직 못 읽었으면 기록된 키와 값을 그대로 보인다", async () => {
    render(<BacktestRunDetail result={result()} environmentFields={null} />);
    const user = userEvent.setup();
    const drawer = screen.getByRole("group", {
      name: "Manifest · 데이터 경고 · 재현성 정보",
    });
    await user.click(
      within(drawer).getByText("Manifest · 데이터 경고 · 재현성 정보"),
    );
    const row = within(drawer)
      .getByText("missing", { exact: true })
      .closest("div")!;
    expect(row).toHaveTextContent("zero");
  });
});

describe("run 상세의 데이터 경고", () => {
  // main #239 가 벤치마크 경고를 원인별 코드로 나눴다. 코드마다 제목이 있어 코드 원문이 제목이 되지 않는다.
  it.each([
    "benchmark.no_bar_at_start",
    "benchmark.suspended_sessions_carried",
    "benchmark.delisted_sessions_frozen",
    "benchmark.invalid_bar_sessions_carried",
    "portfolio.sector_unknown_excluded",
  ])("%s 에 제목이 있다", async (code) => {
    const base = result();
    render(
      <BacktestRunDetail
        result={{
          ...base,
          manifest: {
            ...base.manifest,
            warnings: [{ code, message: "서버 문장", severity: "warning" }],
          },
        }}
      />,
    );
    const user = userEvent.setup();
    const drawer = screen.getByRole("group", {
      name: "Manifest · 데이터 경고 · 재현성 정보",
    });
    await user.click(
      within(drawer).getByText("Manifest · 데이터 경고 · 재현성 정보"),
    );
    const warning = within(drawer).getByText("서버 문장").closest("p")!;
    expect(warning.querySelector("strong")?.textContent).not.toBe(code);
    expect(warning.querySelector("code")?.textContent).toBe(code);
  });

  it("경고 제목을 코드로 고르고 서버 문장과 코드를 함께 보인다", async () => {
    const base = result();
    const sectorMessage =
      "섹터 정보가 없는 종목 3개를 섹터 상한·섹터 중립 계산에서 뺐습니다(프레임 2개).";
    render(
      <BacktestRunDetail
        result={{
          ...base,
          manifest: {
            ...base.manifest,
            warnings: [
              {
                code: "portfolio.sector_unknown_excluded",
                message: sectorMessage,
                severity: "warning",
              },
              // 제목이 없는 새 코드(예: 서버가 나중에 늘린 벤치마크 경고)는 코드를 제목으로 보인다.
              {
                code: "benchmark.future_code",
                message: "새 경고 문장",
                severity: "warning",
              },
            ],
          },
        }}
      />,
    );
    const user = userEvent.setup();
    const drawer = screen.getByRole("group", {
      name: "Manifest · 데이터 경고 · 재현성 정보",
    });
    await user.click(
      within(drawer).getByText("Manifest · 데이터 경고 · 재현성 정보"),
    );

    const sector = within(drawer)
      .getByText("섹터를 모르는 종목은 섹터 제약에서 뺐습니다")
      .closest("p")!;
    expect(sector).toHaveTextContent(sectorMessage);
    expect(sector).toHaveTextContent("portfolio.sector_unknown_excluded");

    const unknown = within(drawer)
      .getByText("benchmark.future_code", { selector: "strong" })
      .closest("p")!;
    expect(unknown).toHaveTextContent("새 경고 문장");
    expect(unknown.querySelector("code")).toBeNull();
  });
});

describe("사용 불가 지표의 이유", () => {
  // 이슈 #241 P3-4: 지표 칸의 사용 불가 사유는 로케일 문구로 보이고, 그 이유를 적은 데이터 경고가
  // 있으면 칸에서 바로 그 경고로 간다. 전에는 "benchmark not available" 원문만 보였고 이유는 접힌
  // manifest 안에만 있었다.
  const unavailableBenchmark = (
    warnings: BacktestRunResult["manifest"]["warnings"],
  ): BacktestRunResult => {
    const base = result();
    return {
      ...base,
      manifest: { ...base.manifest, warnings },
      metric_definitions: [
        {
          metric_id: "benchmark_return",
          label: "Benchmark return",
          category: "benchmark",
          unit: "percent",
          higher_is_better: true,
          nullable: true,
        },
      ],
      metrics: [
        {
          metric_id: "benchmark_return",
          value: null,
          scope: "full",
          sample_count: 120,
          unavailable_reason: "benchmark_not_available",
        },
      ],
    };
  };

  it("사유를 로케일 문구로 보이고 이유를 적은 경고로 이어 준다", async () => {
    render(
      <BacktestRunDetail
        result={unavailableBenchmark([
          {
            code: "benchmark.no_bar_at_start",
            message: "창 시작부터 거래정지 중이었다",
            severity: "warning",
          },
        ])}
      />,
    );
    const row = screen
      .getByRole("table")
      .querySelector("tbody tr") as HTMLElement;
    expect(row).toHaveTextContent("벤치마크 값이 비어 계산할 수 없습니다");
    expect(row).not.toHaveTextContent("benchmark not available");

    const drawer = screen.getByRole("group", {
      name: "Manifest · 데이터 경고 · 재현성 정보",
    });
    expect(drawer).not.toHaveAttribute("open");
    const link = within(row).getByRole("link", {
      name: "데이터 경고에서 이유 보기",
    });
    await userEvent.setup().click(link);

    expect(drawer).toHaveAttribute("open");
    const target = document.getElementById(link.getAttribute("href")!.slice(1));
    expect(target).toHaveTextContent("창 시작부터 거래정지 중이었다");
    expect(target).toHaveTextContent("benchmark.no_bar_at_start");
  });

  it("이유를 적은 경고가 없으면 사유 문구만 보인다", () => {
    render(<BacktestRunDetail result={unavailableBenchmark([])} />);
    const row = screen
      .getByRole("table")
      .querySelector("tbody tr") as HTMLElement;
    expect(row).toHaveTextContent("벤치마크 값이 비어 계산할 수 없습니다");
    expect(within(row).queryByRole("link")).toBeNull();
  });
});
