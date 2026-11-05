import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { useState } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { MarketOverviewBody } from "../../../entities/market";
import type { MarketSectorBody } from "../../../shared/api";
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

const MAIN = new Set(["individual", "foreign", "institution", "other_corporation"]);

const amounts = (values: Partial<Record<(typeof INVESTORS)[number], number>>) =>
  INVESTORS.map((investor) => ({
    investor,
    amount_krw: values[investor] ?? 0,
    group: MAIN.has(investor)
      ? ("main" as const)
      : investor === "resident_foreign"
        ? ("footnote" as const)
        : ("institution_part" as const),
  }));

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
    act(() => tiles[0].focus());
    // 초점이 간 칸은 툴팁으로 등락·이름·유동시총을 보이고 Escape 로 닫힌다
    expect(within(map).getByText(/^유동시총 \d/u)).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(within(map).queryByText(/^유동시총 \d/u)).toBeNull();
    await user.keyboard("{ArrowRight}");
    expect(tiles[1]).toHaveFocus();
    expect(tiles[1].tabIndex).toBe(0);
    await user.keyboard("{Enter}");
    expect(onSector).toHaveBeenCalledWith(expect.stringMatching(/^G\d+$/u));
    // 칸 이름은 방향을 글자로도 말한다
    expect(tiles[0]).toHaveAccessibleName(/(상승|하락)/u);
  });

  it("drills inside the same map, keeping the last level on screen until the next one arrives", async () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
      DOMRect.fromRect({ width: 1000, height: 400 }),
    );
    let reads = 0;
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get(`${API}/api/v1/market/overview`, () => {
        reads += 1;
        return HttpResponse.json(OVERVIEW);
      }),
      http.get(`${API}/api/v1/market/sectors/:code`, async () => {
        await gate;
        return HttpResponse.json({
          status: "available",
          message: null,
          parent: group("G45", "IT", 3_000 * 1e9, -4.88),
          group: group("G4530", "반도체와반도체장비", 2_000 * 1e9, -4.97),
          session: "2026-10-30",
          snapshot_date: "2026-10-30",
          securities: [
            { ticker: "005930", name: "삼성전자", size_weight_krw: 1_500 * 1e9, return_pct: -5.1 },
          ],
        } satisfies MarketSectorBody);
      }),
    );
    const Harness = () => {
      const [sector, setSector] = useState<string>();
      return <MarketOverview sector={sector} onSector={setSector} />;
    };
    const user = userEvent.setup();
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <Harness />
      </QueryClientProvider>,
    );
    const map = await screen.findByRole("group", { name: /섹터맵, 방향키로/u });
    const card = screen.getByRole("article", { name: "섹터맵" });
    await user.click(within(map).getByRole("button", { name: /반도체와반도체장비/u }));
    // 종목이 오기 전: 카드는 그대로이고, 방금 보던 지도가 (읽히지 않는 채로) 남은 위에 불러오는 중이 뜬다 — 빈 칸이 없다
    expect(await within(card).findByRole("status")).toHaveTextContent("시장 데이터를 불러오는 중");
    expect(screen.getByRole("article", { name: /^반도체와반도체장비/u })).toBe(card);
    expect(map).toBeInTheDocument();
    expect(within(card).queryByRole("group", { name: /섹터맵, 방향키로/u })).toBeNull();
    release();
    expect(await within(card).findByRole("group", { name: /종목 지도/u })).toBeInTheDocument();
    expect(map).not.toBeInTheDocument();
    expect(within(card).queryByRole("status")).toBeNull();
    // 드릴다운은 시장 블록을 다시 묻지 않는다
    expect(reads).toBe(1);
  });

  it("comes straight back to the whole map when the user leaves a level that is still loading", async () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
      DOMRect.fromRect({ width: 1000, height: 400 }),
    );
    server.use(
      http.get(`${API}/api/v1/market/sectors/:code`, () => new Promise<never>(() => {})),
    );
    // 줌 전환이 도는 브라우저처럼 Web Animations 를 둔다(jsdom 에는 없다). 전환은 끝나지 않은 채로 둔다.
    Object.defineProperty(HTMLElement.prototype, "animate", {
      configurable: true,
      value: () => ({ finished: new Promise(() => {}), cancel: () => {} }),
    });
    const Harness = () => {
      const [sector, setSector] = useState<string>();
      return <MarketOverview sector={sector} onSector={setSector} />;
    };
    const user = userEvent.setup();
    try {
      render(
        <QueryClientProvider
          client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
        >
          <Harness />
        </QueryClientProvider>,
      );
      const map = await screen.findByRole("group", { name: /섹터맵, 방향키로/u });
      await user.click(within(map).getByRole("button", { name: /반도체와반도체장비/u }));
      const card = screen.getByRole("article", { name: /^반도체와반도체장비/u });
      await user.click(within(card).getByRole("button", { name: "전체 시장" }));
      // 커진 채 남아 있던 지도와 돌아온 지도가 겹쳐 두 벌이 되지 않는다
      // (떠나는 층은 inert 라 역할 질의에 잡히지 않아 이름 속성으로 센다)
      expect(card.querySelectorAll('[aria-label^="섹터맵, 방향키로"]')).toHaveLength(1);
      expect(within(card).queryByRole("status")).toBeNull();
    } finally {
      delete (HTMLElement.prototype as { animate?: unknown }).animate;
    }
  });

  it("hides a tile name that does not fit as drawn but keeps it in the accessible name", async () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
      DOMRect.fromRect({ width: 1000, height: 400 }),
    );
    // jsdom 은 배치를 하지 않는다 — 그려진 이름 한 단어가 칸보다 넓은 경우를 흉내 낸다
    Object.defineProperty(HTMLElement.prototype, "scrollWidth", {
      configurable: true,
      get(this: HTMLElement) {
        return this.textContent === "기술하드웨어와장비" ? 999 : 0;
      },
    });
    try {
      renderOverview();
      const map = await screen.findByRole("group", { name: /섹터맵, 방향키로/u });
      expect(within(map).getByText("기술하드웨어와장비")).not.toBeVisible();
      expect(within(map).getByText("반도체와반도체장비")).toBeVisible();
      expect(
        within(map).getByRole("button", { name: /기술하드웨어와장비 하락 7\.00%/u }),
      ).toBeInTheDocument();
    } finally {
      delete (HTMLElement.prototype as { scrollWidth?: unknown }).scrollWidth;
    }
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

  it("says why a sector read failed, retries it, and reads stock tiles without buttons", async () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      width: 1000,
      height: 400,
      top: 0,
      left: 0,
      right: 1000,
      bottom: 400,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    let failing = true;
    server.use(
      http.get(`${API}/api/v1/market/sectors/:code`, () => {
        if (failing) return HttpResponse.json({ detail: "boom" }, { status: 500 });
        return HttpResponse.json({
          status: "available",
          message: null,
          parent: group("G45", "IT", 3_000 * 1e9, -4.88),
          group: group("G4530", "반도체와반도체장비", 2_000 * 1e9, -4.97),
          session: "2026-10-30",
          snapshot_date: "2026-10-30",
          securities: [
            { ticker: "005930", name: "삼성전자", size_weight_krw: 1_500 * 1e9, return_pct: -5.1 },
            { ticker: "000660", name: "SK하이닉스", size_weight_krw: 500 * 1e9, return_pct: -4.5 },
          ],
        } satisfies MarketSectorBody);
      }),
    );
    const user = userEvent.setup();
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <MarketOverview sector="G4530" onSector={vi.fn()} />
      </QueryClientProvider>,
    );
    // 일시적인 실패를 "그 업종이 없다"고 말하지 않는다
    const failed = await screen.findByRole("alert");
    expect(failed).toHaveTextContent("시장 데이터를 불러오지 못했어요.");
    failing = false;
    await user.click(within(failed).getByRole("button", { name: "다시 시도" }));
    const stocks = await screen.findByRole("group", { name: /종목 지도/u });
    // 종목 칸은 누를 일이 없다 — 버튼이 아니라 이름과 등락만 읽힌다
    expect(within(stocks).queryAllByRole("button")).toHaveLength(0);
    expect(within(stocks).getAllByRole("img").map((tile) => tile.getAttribute("aria-label"))).toEqual(
      expect.arrayContaining([expect.stringContaining("삼성전자")]),
    );
  });
});
