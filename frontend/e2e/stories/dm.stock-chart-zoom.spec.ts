/**
 * 정동민의 스토리 e2e — 개별종목차트를 트레이딩뷰처럼 확대·이동하고, 오른쪽 축 배지는 고른 선만 보인다(US-DM-20).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다. 연습용(mock) 원천의 코스피(`practice:`
 * 접두)로 돈다. 보기 창 계산(커서 중심 확대·한도)은 단위 테스트가 손계산으로 보고, 여기서는 화면 흐름 — 휠·끌기·키보드가
 * 보이는 구간을 바꾸고 그 구간이 날짜 칸·주소에 오며 이력을 쌓지 않는 것 — 을 본다. 테스트마다 시작할 때 API로 그 종목의
 * 선을 지운다.
 */
import { expect, test, type Locator, type Page } from "@playwright/test";

import { deleteChartLine, getChartCandles, listChartLines } from "../../src/shared/api/generated";
import { apiClient, requireData } from "../workbench-helpers";

const KOSPI = { kind: "index" as const, code: "practice:KOSPI:코스피" };

const clearLines = async () => {
  const listed = requireData(
    (await listChartLines({ client: apiClient, query: KOSPI })).data,
    "list chart lines",
  );
  for (const line of listed.lines)
    await deleteChartLine({ client: apiClient, path: { line_id: line.line_id } });
};

/** 코스피 차트를 연다(내비 → 종목 검색 → 결과). 처음 주기는 주봉이다. */
const openKospi = async (page: Page) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "주 메뉴" }).getByRole("link", { name: "개별종목차트" }).click();
  await page.getByRole("searchbox", { name: "종목" }).fill("코스피");
  await page.getByRole("button", { name: /^코스피 .*지수$/u }).click();
  await expect(page.getByRole("group", { name: "코스피 주봉 캔들 차트와 선" })).toBeVisible();
};

/** 그림 칸 안의 비율 자리(오른쪽 가격 축 84px·위 12px·아래 날짜 축 28px를 뺀 칸). */
const plotPoint = async (chart: Locator, fx: number, fy: number) => {
  const box = await chart.boundingBox();
  if (box === null) throw new Error("chart has no bounding box");
  return { x: box.x + (box.width - 84) * fx, y: box.y + 12 + (box.height - 40) * fy };
};

/** 표시 기간 날짜 칸 두 개의 지금 값. */
const shownRange = async (page: Page) => {
  const conditions = page.getByRole("group", { name: "차트 조건" });
  return {
    start: await conditions.getByLabel("시작일").inputValue(),
    end: await conditions.getByLabel("끝일").inputValue(),
  };
};

test(
  "US-DM-20 휠·끌기·키보드로 코스피 일봉을 확대·이동하면 날짜 칸이 따라오고 더블클릭·Home이 고른 기간으로 되돌린다",
  { tag: ["@story", "@US-DM-20"] },
  async ({ page }) => {
    await clearLines();
    await openKospi(page);
    const conditions = page.getByRole("group", { name: "차트 조건" });
    await conditions.getByRole("button", { name: "일봉" }).click();
    const chart = page.getByRole("group", { name: "코스피 일봉 캔들 차트와 선" });
    await expect(chart).toBeVisible();
    const twoYears = conditions.getByRole("button", { name: "2년" });
    await expect(twoYears).toHaveAttribute("aria-pressed", "true");
    const base = await shownRange(page);
    const candles = requireData(
      (await getChartCandles({ client: apiClient, query: { ...KOSPI, timeframe: "day" } })).data,
      "candles",
    ).candles;
    // 날짜 칸 구간에 걸친 봉 수(화면이 그 구간의 봉을 그린다)
    const barsIn = ({ start, end }: { start: string; end: string }) =>
      candles.filter((candle) => candle.end >= start && candle.start <= end).length;

    // 휠을 위로: 커서 자리 중심 확대 — 보이는 봉이 줄고 날짜 칸·주소가 그 구간으로, 기간 버튼 눌림이 풀린다
    const centre = await plotPoint(chart, 0.5, 0.5);
    await page.mouse.move(centre.x, centre.y);
    await page.mouse.wheel(0, -400);
    await expect(twoYears).toHaveAttribute("aria-pressed", "false");
    const zoomed = await shownRange(page);
    expect(zoomed.start > base.start).toBe(true);
    expect(barsIn(zoomed)).toBeLessThan(barsIn(base) * 0.7);
    await expect(page).toHaveURL(new RegExp(`start=${zoomed.start}`, "u"));
    // 보이는 구간은 화면 낭독기에도 알린다
    await expect(page.getByText(`보이는 구간 ${zoomed.start} ~ ${zoomed.end}`)).toBeAttached();

    // 확대는 이력을 쌓지 않는다: 뒤로 가기는 앞 조건(주봉)으로, 앞으로 가기는 확대한 일봉으로
    await page.goBack();
    await expect(conditions.getByRole("button", { name: "주봉" })).toHaveAttribute("aria-pressed", "true");
    await page.goForward();
    await expect(chart).toBeVisible();
    await expect.poll(() => shownRange(page)).toEqual(zoomed);

    // 빈 곳을 오른쪽으로 끌면 앞(과거) 구간으로 옮겨진다
    const from = await plotPoint(chart, 0.4, 0.5);
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move(from.x + 240, from.y, { steps: 8 });
    await page.mouse.up();
    await expect.poll(async () => (await shownRange(page)).start < zoomed.start).toBe(true);

    // 더블클릭: 고른 기간(2년)으로 돌아간다
    const empty = await plotPoint(chart, 0.5, 0.5);
    await page.mouse.dblclick(empty.x, empty.y);
    await expect(twoYears).toHaveAttribute("aria-pressed", "true");
    await expect.poll(() => shownRange(page)).toEqual(base);

    // 키보드: 차트에 초점을 두고 + 확대, ← 앞 구간, Home 되돌림
    await chart.focus();
    await page.keyboard.press("+");
    await page.keyboard.press("+");
    await expect(twoYears).toHaveAttribute("aria-pressed", "false");
    const keyed = await shownRange(page);
    expect(barsIn(keyed)).toBeLessThan(barsIn(base));
    await page.keyboard.press("ArrowLeft");
    await expect.poll(async () => (await shownRange(page)).start < keyed.start).toBe(true);
    await page.keyboard.press("Home");
    await expect(twoYears).toHaveAttribute("aria-pressed", "true");
    await expect.poll(() => shownRange(page)).toEqual(base);
  },
);

test(
  "US-DM-20 오른쪽 축 배지는 현재가와 고른 선의 레벨만 보인다",
  { tag: ["@story", "@US-DM-20"] },
  async ({ page }) => {
    await clearLines();
    await openKospi(page);
    const chart = page.getByRole("group", { name: "코스피 주봉 캔들 차트와 선" });
    const layers = page.getByRole("complementary", { name: "레이어" });
    // 배지는 축 장식이라(aria-hidden) CSS로 센다. 처음엔 현재가 하나다
    const badges = chart.locator(".chart-badge");
    await expect(badges).toHaveCount(1);

    // 되돌림(레벨 7개)과 수평선을 긋는다 — 마지막에 그은 수평선이 골라져 있다
    await page.getByRole("button", { name: "피보나치 되돌림" }).click();
    for (const [fx, fy] of [
      [0.3, 0.2],
      [0.7, 0.8],
    ] as const) {
      const at = await plotPoint(chart, fx, fy);
      await page.mouse.move(at.x, at.y);
      await page.mouse.click(at.x, at.y);
    }
    const retracement = layers.getByRole("button", { name: /^되돌림 \S+ → \S+$/u });
    await expect(retracement).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "수평 지지·저항선" }).click();
    const at = await plotPoint(chart, 0.5, 0.45);
    await page.mouse.move(at.x, at.y);
    await page.mouse.click(at.x, at.y);
    const hline = layers.getByRole("button", { name: /^수평선 [\d,.]+$/u });
    await expect(hline).toHaveAttribute("aria-pressed", "true");

    // 고른 수평선: 현재가 + 그 선의 레벨 하나(되돌림 레벨은 축에 쌓이지 않는다)
    await expect(badges).toHaveCount(2);
    const price = ((await hline.textContent()) ?? "").replace(/^수평선 /u, "");
    await expect(chart.locator(".chart-badge--mine")).toHaveText(price);

    // 되돌림을 고르면 그 선의 레벨들, 고르기를 풀면 현재가만
    await retracement.click();
    await expect(chart.locator(".chart-badge--mine").first()).toBeVisible();
    expect(await badges.count()).toBeGreaterThan(2);
    await retracement.click();
    await expect(badges).toHaveCount(1);
  },
);
