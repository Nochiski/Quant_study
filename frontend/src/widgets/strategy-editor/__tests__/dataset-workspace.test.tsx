import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";

import type { ResearchPanelPreviewRequest } from "../../../entities/dataset";
import type { StrategySpec } from "../../../entities/strategy";
import { StrategyEditor } from "../ui/strategy-editor";

const template: StrategySpec = {
  identity: { strategy_id: "draft", revision: 0, schema_version: "1.0" },
  title: "새 팩터 전략",
  description: "",
  data: {
    market: "KRX",
    start: "2021-09-03",
    end: "2026-09-03",
    universe_id: "krx.common-stock",
    frequency: "daily",
  },
  eligibility: { rules: [] },
  factors: {
    factors: [
      {
        factor_id: "price.close",
        label: "종가",
        direction: "high",
        weight: 1,
        graph: {
          nodes: [{ kind: "field", node_id: "close", field_id: "price.close" }],
          output_node_id: "close",
        },
      },
    ],
  },
  signal: { method: "weighted_sum", entry_percentile: 0.1 },
  portfolio: {
    side: "long_only",
    selection_count: 20,
    weighting: "equal",
    rebalance: "monthly",
  },
  risk: {
    gross_exposure: 1,
    net_exposure: 1,
    max_name_weight: 0.1,
    max_sector_weight: 0.3,
  },
  execution: {
    timing: "next_open",
    order_style: "market",
    participation_rate: 0.1,
    fee_bps: 15,
    slippage_bps: 10,
  },
  parameters: [],
};

const coverage = {
  starts_on: "2024-01-02",
  ends_on: "2024-01-12",
  venues: ["XKRX"],
  estimated_coverage_pct: 91,
  supported_cell_kinds: [
    "observed",
    "source_omitted_zero",
    "missing",
    "not_collected",
    "coverage_gap",
  ],
  point_in_time: true,
  requires_confirmation: true,
} as const;

let catalogSearch: string | null = null;
let lastPanelRequest: ResearchPanelPreviewRequest | null = null;

const server = setupServer(
  http.get("http://localhost:8000/api/v1/strategies/template", () =>
    HttpResponse.json(template),
  ),
  http.get("http://localhost:8000/api/v1/equity/catalog", ({ request }) => {
    catalogSearch = new URL(request.url).searchParams.get("search");
    return HttpResponse.json({
      snapshot: {
        snapshot_id: "mock-equity-v0.2-20260903",
        schema_version: "equity-v0.2-mock",
        built_at: "2026-09-03T00:00:00Z",
        source: "deterministic-memory-fixture",
        point_in_time: true,
        dataset_revisions: [],
      },
      fields: [
        {
          field_id: "price.close",
          dataset_id: "price_daily",
          label: "종가",
          unit: "KRW",
          value_type: "price",
          frequency: "daily",
          available_date_basis: "session close",
          recommended_lag_sessions: 0,
          description: "KRX 원주가",
          disclosure_basis: "정규장 종가 확정 시점",
          evidence: "KRX 일별매매정보",
          coverage: { ...coverage, estimated_coverage_pct: 100 },
        },
        {
          field_id: "flow.foreign_net_buy",
          dataset_id: "flow_daily",
          label: "외국인 순매수",
          unit: "KRW",
          value_type: "amount",
          frequency: "daily",
          available_date_basis: "session",
          recommended_lag_sessions: 0,
          description: "투자자별 순매수",
          disclosure_basis: "거래일별 집계",
          evidence: "KRX 투자자별 거래실적",
          coverage,
        },
        {
          field_id: "consensus.forward_eps",
          dataset_id: "consensus_daily",
          label: "12개월 선행 EPS",
          unit: "KRW/share",
          value_type: "price",
          frequency: "daily",
          available_date_basis: "first_seen_fetched_date",
          recommended_lag_sessions: 0,
          description: "판본을 보존하는 컨센서스",
          disclosure_basis: "최초 수집일",
          evidence: "수집시각 로그",
          coverage,
        },
      ],
      total: 3,
      page: 1,
      page_size: 4,
      page_count: 1,
      facets: {
        dataset_ids: ["price_daily", "flow_daily", "consensus_daily"],
        units: ["KRW", "KRW/share"],
        frequencies: ["daily"],
      },
    });
  }),
  http.post("http://localhost:8000/api/v1/equity/universe/preview", () =>
    HttpResponse.json({
      universe: {
        points: [
          {
            session: "2024-01-04",
            members: [
              {
                security_id: "sec-005930-1",
                ticker: "005930",
                name: "삼성전자",
                venue: "XKRX",
              },
              {
                security_id: "sec-000660-1",
                ticker: "000660",
                name: "SK하이닉스",
                venue: "XKRX",
              },
            ],
            coverage: "observed",
          },
        ],
        status: "ok",
        snapshot_id: "mock-equity-v0.2-20260903",
      },
      coverage: {
        session_count: 1,
        covered_session_count: 1,
        gap_session_count: 0,
        first_session: "2024-01-04",
        last_session: "2024-01-04",
        minimum_members: 2,
        maximum_members: 2,
        average_members: 2,
      },
    }),
  ),
  http.post(
    "http://localhost:8000/api/v1/equity/panel/preview",
    async ({ request }) => {
      lastPanelRequest = (await request.json()) as ResearchPanelPreviewRequest;
      const confirmed = lastPanelRequest.confirmed_warning_ids?.length !== 0;
      const warnings = [
        {
          warning_id: "coverage_incomplete:flow.foreign_net_buy",
          code: "coverage_incomplete",
          message: "외국인 순매수 커버리지가 완전하지 않습니다.",
          severity: "warning",
          field_id: "flow.foreign_net_buy",
          requires_confirmation: true,
        },
      ];
      return HttpResponse.json({
        panel: {
          cells: confirmed
            ? [
                {
                  as_of: "2024-01-04",
                  security_id: "sec-005930-1",
                  field_id: "flow.foreign_net_buy",
                  source_effective_date: "2024-01-04",
                  available_date: "2024-01-04",
                  value: 0,
                  kind: "observed",
                },
                {
                  as_of: "2024-01-04",
                  security_id: "sec-000660-1",
                  field_id: "flow.foreign_net_buy",
                  source_effective_date: "2024-01-04",
                  available_date: "2024-01-04",
                  value: 0,
                  kind: "source_omitted_zero",
                },
                {
                  as_of: "2024-01-04",
                  security_id: "sec-missing",
                  field_id: "flow.foreign_net_buy",
                  source_effective_date: "2024-01-04",
                  available_date: "2024-01-04",
                  value: null,
                  kind: "missing",
                },
                {
                  as_of: "2024-01-04",
                  security_id: "sec-not-collected",
                  field_id: "flow.foreign_net_buy",
                  source_effective_date: "2024-01-04",
                  available_date: "2024-01-04",
                  value: null,
                  kind: "not_collected",
                },
                {
                  as_of: "2024-01-04",
                  security_id: "sec-gap",
                  field_id: "flow.foreign_net_buy",
                  source_effective_date: "2024-01-04",
                  available_date: "2024-01-04",
                  value: null,
                  kind: "coverage_gap",
                },
                {
                  as_of: "2024-01-04",
                  security_id: "sec-005930-1",
                  field_id: "consensus.forward_eps",
                  source_effective_date: "2024-01-02",
                  available_date: "2024-01-03",
                  value: 5000,
                  kind: "observed",
                },
              ]
            : [],
          status: confirmed ? "ok" : "confirmation_required",
          snapshot_id: "mock-equity-v0.2-20260903",
          warnings: [],
        },
        cost: {
          session_count: 1,
          requested_rows: 2,
          requested_columns: 3,
          estimated_cells: 6,
          estimated_bytes: 384,
          returned_rows: confirmed ? 5 : 0,
          returned_columns: confirmed ? 2 : 0,
          returned_cells: confirmed ? 6 : 0,
        },
        warnings,
        confirmation_required: !confirmed,
        truncated: false,
      });
    },
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  catalogSearch = null;
  lastPanelRequest = null;
});
afterAll(() => server.close());

test("PIT 필드 선택부터 위험 확인과 결측 상태 구분까지 이어진다", async () => {
  const user = userEvent.setup();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <StrategyEditor />
    </QueryClientProvider>,
  );

  await user.click(await screen.findByRole("button", { name: "1 데이터" }));
  await user.click(screen.getByRole("button", { name: "목업 구간 사용" }));
  await user.clear(screen.getByLabelText("시작일"));
  await user.type(screen.getByLabelText("시작일"), "2024-01-04");
  await user.clear(screen.getByLabelText("종료일"));
  await user.type(screen.getByLabelText("종료일"), "2024-01-04");

  await user.type(screen.getByLabelText("필드 검색"), "흐름");
  await waitFor(() => expect(catalogSearch).toBe("흐름"));
  await user.click(screen.getByRole("checkbox", { name: /외국인 순매수/ }));
  await user.click(screen.getByRole("checkbox", { name: /12개월 선행 EPS/ }));
  await user.click(
    screen.getAllByText("필드 근거와 사용 조건", { selector: "summary" })[1],
  );
  expect(screen.getByText("KRX 투자자별 거래실적")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "데이터 미리보기" }));
  expect(
    await screen.findByText("확인이 필요한 데이터 위험"),
  ).toBeInTheDocument();
  expect(screen.queryByText("실제 0")).not.toBeInTheDocument();

  await user.click(
    screen.getByRole("button", { name: "위험을 확인하고 미리보기" }),
  );

  expect(await screen.findByText("실제 0")).toBeInTheDocument();
  expect(screen.getByText("원천 생략 0")).toBeInTheDocument();
  expect(screen.getByText("결측")).toBeInTheDocument();
  expect(screen.getByText("미수집")).toBeInTheDocument();
  expect(screen.getAllByText("커버리지 공백")).toHaveLength(2);
  expect(screen.getByText("5,000")).toBeInTheDocument();
  expect(screen.queryByText("5,400")).not.toBeInTheDocument();
  expect(lastPanelRequest?.query.end).toBe("2024-01-04");
  expect(lastPanelRequest?.confirmed_warning_ids).toEqual([
    "coverage_incomplete:flow.foreign_net_buy",
  ]);
});
