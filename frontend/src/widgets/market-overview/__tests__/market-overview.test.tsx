import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { MarketOverviewBody } from "../../../entities/market";
import { MarketOverview } from "..";

const API = "http://localhost:8000";
const EOK = 100_000_000;
const INVESTORS = [
  "individual",
  "foreign",
  "institution",
  "other_corporation",
  "resident_foreign",
  "financial_investment",
  "pension",
  "investment_trust",
  "private_equity",
  "insurance",
  "bank",
  "other_financial",
  "government",
] as const;

const amounts = (values: Partial<Record<(typeof INVESTORS)[number], number>>) =>
  INVESTORS.map((investor) => ({ investor, amount_krw: values[investor] ?? 0 }));

const total = (
  period: "day" | "five_sessions" | "twenty_sessions",
  sessions: number,
  values: Partial<Record<(typeof INVESTORS)[number], number>>,
  summary: string,
) => ({
  period,
  start: "2026-10-26",
  end: "2026-10-30",
  sessions,
  amounts: amounts(values),
  summary,
});

const group = (code: string, name: string, size: number, returnPct: number) => ({
  code,
  name,
  size_weight_krw: size,
  return_weight_krw: size,
  return_pct: returnPct,
  securities: 3,
  untraded: 0,
});

const OVERVIEW: MarketOverviewBody = {
  status: "available",
  message: null,
  session: "2026-10-30",
  indices: [
    {
      index_id: "kospi",
      index_name: "코스피",
      issue: null,
      summary: {
        session: "2026-10-30",
        close: 5730.74,
        change: -206.92,
        change_pct: -3.48,
        value_krw: 20_200_000_000_000,
        high_close: 7080.92,
        low_close: 5469.47,
        history: [
          { session: "2026-10-29", close: 5937.66 },
          { session: "2026-10-30", close: 5730.74 },
        ],
      },
    },
  ],
  flows: [
    {
      market: "KOSPI",
      issue: null,
      coverage: {
        session: "2026-10-30",
        grid_securities: 829,
        measured_securities: 828,
        mktcap_share: 0.99,
      },
      totals: [
        total(
          "day",
          1,
          { individual: 4632 * EOK, foreign: -14542 * EOK, institution: 7231 * EOK },
          "코스피 10-30: 외국인이 1조 4,542억 원어치를 가장 많이 더 팔았고, 개인·기관이 더 샀어요.",
        ),
        total("five_sessions", 5, { foreign: -581 * EOK }, "코스피 최근 5거래일: 요약"),
        total("twenty_sessions", 20, { foreign: -179349 * EOK }, "코스피 최근 20거래일: 요약"),
      ],
      days: [{ session: "2026-10-30", amounts: amounts({ foreign: -14542 * EOK }) }],
    },
    {
      market: "KOSDAQ",
      issue: { code: "coverage_low", message: "10-30은 수급이 측정된 종목이 적어요." },
      coverage: null,
      totals: [],
      days: [],
    },
  ],
  sectors: {
    issue: null,
    sector_map: {
      snapshot_date: "2026-10-30",
      session: "2026-10-30",
      securities: 10,
      untraded: 0,
      parents: [
        {
          group: group("G45", "IT", 3_000 * 1e9, -4.88),
          children: [
            group("G4530", "반도체와반도체장비", 2_000 * 1e9, -4.97),
            group("G4520", "기술하드웨어와장비", 1_000 * 1e9, -7),
          ],
        },
        {
          group: group("G40", "금융", 1_500 * 1e9, 0.4),
          children: [
            group("G4010", "은행", 1_000 * 1e9, 0.28),
            group("G4020", "보험", 500 * 1e9, -0.48),
          ],
        },
      ],
    },
  },
};

const server = setupServer(
  http.get(`${API}/api/v1/market/overview`, () => HttpResponse.json(OVERVIEW)),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  vi.restoreAllMocks();
  window.localStorage.clear();
});
afterAll(() => server.close());

const renderOverview = (onSector = vi.fn()) => {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MarketOverview sector={undefined} onSector={onSector} />
    </QueryClientProvider>,
  );
  return onSector;
};

describe("MarketOverview", () => {
  it("switches the investor flow by market and period and keeps the coverage reason", async () => {
    const user = userEvent.setup();
    renderOverview();
    const flow = await screen.findByRole("article", { name: "투자자별 순매수" });
    expect(flow).toHaveTextContent("외국인이 1조 4,542억 원어치를 가장 많이 더 팔았고");
    expect(flow).toHaveTextContent("1.45조");
    expect(flow).toHaveTextContent("828/829개");
    await user.click(within(flow).getByRole("button", { name: "20일" }));
    expect(flow).toHaveTextContent("17.9조");
    expect(within(flow).getByRole("button", { name: "20일" })).toHaveAttribute("aria-pressed", "true");
    await user.click(within(flow).getByRole("button", { name: "코스닥" }));
    // 측정 비중이 낮은 시장은 합 대신 사유를 보인다
    expect(flow).toHaveTextContent("10-30은 수급이 측정된 종목이 적어요.");
    expect(flow).not.toHaveTextContent("17.9조");
  });

  it("reads the sector map with one tab stop and opens the tile the user picks", async () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      width: 1000,
      height: 320,
      top: 0,
      left: 0,
      right: 1000,
      bottom: 320,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    const user = userEvent.setup();
    const onSector = renderOverview();
    const map = await screen.findByRole("group", { name: /섹터맵, 방향키로/u });
    const tiles = within(map).getAllByRole("button");
    expect(tiles.filter((tile) => tile.tabIndex === 0)).toHaveLength(1);
    tiles[0].focus();
    await user.keyboard("{ArrowRight}");
    expect(tiles[1]).toHaveFocus();
    expect(tiles[1].tabIndex).toBe(0);
    await user.keyboard("{Enter}");
    expect(onSector).toHaveBeenCalledWith(expect.stringMatching(/^G\d+$/u));
    // 칸 이름은 방향을 글자로도 말한다
    expect(tiles[0]).toHaveAccessibleName(/(상승|하락)/u);
  });

  it("falls back to the movers list when the card is narrow and remembers a collapse", async () => {
    const user = userEvent.setup();
    renderOverview();
    const sector = await screen.findByRole("article", { name: "섹터맵" });
    await waitFor(() => expect(sector).toHaveTextContent("많이 내린 세부 업종"));
    expect(within(sector).queryByRole("group", { name: /섹터맵, 방향키로/u })).toBeNull();
    await user.click(screen.getByRole("button", { name: "시장 접기" }));
    expect(screen.queryByRole("article", { name: "섹터맵" })).toBeNull();
    expect(window.localStorage.getItem("home.market.collapsed")).toBe("1");
    expect(screen.getByRole("button", { name: "시장 펼치기" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });
});
