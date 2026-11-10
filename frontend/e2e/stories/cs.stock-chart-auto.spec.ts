/**
 * 김철수의 스토리 e2e — 개별종목차트 자동 찾기(US-CS-16 채널, US-CS-17 수평 구간·되돌림과 근거, US-CS-18 중첩).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/cs.md`다. 연습용(mock) 원천의 코스피로 돈다 — 대상
 * 코드가 `practice:` 접두라 실데이터 선과 섞이지 않는다. 판정·후보·레벨 가격의 규칙은 backend 테스트가 보고, 여기서는
 * 화면이 서버 응답을 그대로 보이고 고른 것을 그대로 저장하는지 본다. 테스트마다 시작할 때 API로 그 종목의 선을 지운다.
 */
import { expect, test, type Page } from "@playwright/test";

import {
  createChartLine,
  deleteChartLine,
  getChartRegime,
  getChartRetracement,
  getChartZones,
  listChartLines,
} from "../../src/shared/api/generated";
import { apiClient, requireData } from "../workbench-helpers";

const KOSPI = { kind: "index" as const, code: "practice:KOSPI:코스피" };

// 연습용 코스피 일봉에서 판정이 갈리는 구간(backend 연습용 원천의 고정 seed 계열에서 고른 날짜)
const FALLING = { start: "2024-10-07", end: "2025-04-21" };
const SIDEWAYS = { start: "2025-06-16", end: "2025-08-11" };

const clearLines = async () => {
  const listed = requireData((await listChartLines({ client: apiClient, query: KOSPI })).data, "list chart lines");
  for (const line of listed.lines) await deleteChartLine({ client: apiClient, path: { line_id: line.line_id } });
};

const savedLines = async () =>
  requireData((await listChartLines({ client: apiClient, query: KOSPI })).data, "list chart lines").lines;

const indexPrice = (value: number) =>
  value.toLocaleString("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** 코스피 차트를 주소로 연다. 화면은 자동 찾기로 열린다. */
const openKospi = async (page: Page, timeframe: "day" | "week") => {
  const search = new URLSearchParams({ ...KOSPI, tf: timeframe });
  await page.goto(`/research/chart?${search.toString()}`);
  const name = timeframe === "day" ? "코스피 일봉 캔들 차트와 선" : "코스피 주봉 캔들 차트와 선";
  const chart = page.getByRole("group", { name });
  await expect(chart).toBeVisible();
  await expect(page.getByRole("group", { name: "분석 방식" }).getByRole("button", { name: "자동 찾기" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  return chart;
};

const layersOf = (page: Page) => page.getByRole("complementary", { name: "레이어" });

/** 열린 패널 표에서 행 머리가 `header`인 줄. */
const levelRow = (page: Page, header: string) =>
  layersOf(page)
    .getByRole("row")
    .filter({ has: page.getByRole("rowheader", { name: header, exact: true }) });

/** 열린 패널의 분석 구간 날짜 칸. */
const windowFields = (page: Page) => ({
  start: layersOf(page).getByLabel("시작 (봉 날짜)"),
  end: layersOf(page).getByLabel("끝 (봉 날짜)"),
});

test(
  "US-CS-16 판정 구간을 끌거나 Shift+끌기로 새로 그어 국면 판정을 보고, 박스권이면 수평 구간을 권받고, 하락 판정에서 첫 점을 골라 추천 채널을 추가한다",
  { tag: ["@story", "@US-CS-16"] },
  async ({ page }) => {
    await clearLines();
    const chart = await openKospi(page, "day");
    const layers = layersOf(page);

    await layers.getByRole("button", { name: "채널", exact: true }).click();
    await expect(page.getByRole("heading", { level: 2, name: "피보나치 채널 만들기" })).toBeFocused();

    // 기본 판정 구간은 가운데 제안 칩(일봉 120봉)이고, 판정·문장·규칙은 서버 응답 그대로다
    const regime = requireData(
      (await getChartRegime({ client: apiClient, query: { ...KOSPI, timeframe: "day" } })).data,
      "regime",
    );
    await expect(layers.getByRole("button", { name: "최근 120봉 · 기본" })).toHaveAttribute("aria-pressed", "true");
    await expect(windowFields(page).start).toHaveValue(regime.window.start);
    // 판정 구간은 차트 위 음영이다(그림 장식이라 CSS로 본다)
    await expect(chart.locator(".analysis-window__band")).toBeVisible();
    await expect(layers.getByText(regime.sentence)).toBeVisible();
    await expect(layers.getByText(`일봉 ${regime.window.bars}개`)).toBeVisible();
    await layers.getByText("판정 규칙").click();
    for (const rule of regime.rules) await expect(layers.getByText(rule)).toBeVisible();

    // 판정 구간 시작 테두리: 키보드 ← 한 번 = 한 봉 앞, 제안 칩 눌림이 풀리고 봉 수가 하나 는다
    const startEdge = chart.getByRole("slider", { name: "판정 구간 시작" });
    await startEdge.focus();
    await page.keyboard.press("ArrowLeft");
    await expect(layers.getByText(`일봉 ${regime.window.bars + 1}개`)).toBeVisible();
    await expect(layers.getByRole("button", { name: "최근 120봉 · 기본" })).toHaveAttribute("aria-pressed", "false");
    const nudged = await windowFields(page).start.inputValue();
    expect(nudged < regime.window.start).toBe(true);
    await expect(startEdge).toHaveAttribute("aria-valuetext", nudged);

    // 마우스로 끌면 봉 단위로 붙어 시작이 더 앞으로 가고, 놓는 즉시 다시 판정한다
    const box = await startEdge.boundingBox();
    if (box === null) throw new Error("window edge has no bounding box");
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 - 60, box.y + box.height / 2, { steps: 6 });
    await page.mouse.up();
    await expect.poll(() => windowFields(page).start.inputValue()).not.toBe(nudged);
    const dragged = await windowFields(page).start.inputValue();
    expect(dragged < nudged).toBe(true);
    await expect(startEdge).toHaveAttribute("aria-valuetext", dragged);

    // Shift를 누른 채 차트를 끌면 누른 봉부터 놓은 봉까지가 새 판정 구간이다 — 끄는 동안 음영이 따라오고, 놓으면 판정이
    // 다시 오며, 차트는 옮겨지지 않는다
    const plot = await chart.locator(".drawing-surface__capture").boundingBox();
    if (plot === null) throw new Error("plot has no bounding box");
    const sweepY = plot.y + plot.height / 2;
    const pressX = plot.x + plot.width * 0.25;
    const endEdge = chart.getByRole("slider", { name: "판정 구간 끝" });
    const endBefore = await endEdge.getAttribute("aria-valuetext");
    const barsBefore = await layers.getByText(/^일봉 \d+개$/u).textContent();
    await page.mouse.move(pressX, sweepY);
    await page.keyboard.down("Shift");
    await page.mouse.down();
    await page.mouse.move(plot.x + plot.width * 0.55, sweepY, { steps: 8 });
    await expect(endEdge).not.toHaveAttribute("aria-valuetext", endBefore ?? "");
    await page.mouse.up();
    await page.keyboard.up("Shift");
    await expect.poll(() => windowFields(page).start.inputValue()).not.toBe(dragged);
    const swept = { start: await windowFields(page).start.inputValue(), end: await windowFields(page).end.inputValue() };
    expect(swept.start < swept.end).toBe(true);
    expect(swept.end < regime.window.end).toBe(true);
    await expect(startEdge).toHaveAttribute("aria-valuetext", swept.start);
    await expect(endEdge).toHaveAttribute("aria-valuetext", swept.end);
    await expect(layers.getByText(/^일봉 \d+개$/u)).not.toHaveText(barsBefore ?? "");
    // 차트가 옮겨졌다면 누른 봉의 테두리가 누른 자리에서 벗어난다(날짜 칸 쓰기는 300ms 늦어 그것으로는 못 본다)
    const startBox = await startEdge.boundingBox();
    if (startBox === null) throw new Error("window edge has no bounding box");
    expect(Math.abs(startBox.x + startBox.width / 2 - pressX)).toBeLessThan(12);

    // 박스권 구간: 채널을 추천하지 않고(그리지 않음) 수평 구간을 권한다
    // 앞의 Shift+끌기로 끝이 박스권 시작보다 앞이라 끝부터 옮긴다(시작은 끝을 넘지 못한다)
    await windowFields(page).end.fill(SIDEWAYS.end);
    await windowFields(page).start.fill(SIDEWAYS.start);
    await expect(layers.getByText("박스권", { exact: true })).toBeVisible();
    await expect(layers.getByRole("radio", { name: "그리지 않음" })).toBeChecked();
    await layers.getByRole("button", { name: "수평 구간 만들기" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "수평 지지·저항 구간 만들기" })).toBeFocused();
    await expect(windowFields(page).start).toHaveValue(SIDEWAYS.start);
    await page.keyboard.press("Escape");
    await expect(layers.getByRole("button", { name: "채널", exact: true })).toBeFocused();

    // 하락 판정 구간: 하락 채널이 추천으로 골라져 있다. 다른 방향을 고르면 직접 고름 경고가 뜬다
    await layers.getByRole("button", { name: "채널", exact: true }).click();
    await windowFields(page).start.fill(FALLING.start);
    await windowFields(page).end.fill(FALLING.end);
    await expect(layers.getByText("하락", { exact: true })).toBeVisible();
    await expect(layers.getByRole("radio", { name: /^하락 채널.*추천/u })).toBeChecked();
    await expect(layers.getByRole("radio", { name: /^상승 채널.*추천/u })).toHaveCount(0);
    await layers.getByRole("radio", { name: /^상승 채널/u }).check();
    await expect(layers.getByText("추천(하락 채널)과 다른 방향이에요.", { exact: false })).toBeVisible();
    await layers.getByRole("radio", { name: /^하락 채널/u }).check();
    await expect(layers.getByText("추천(하락 채널)과 다른 방향이에요.", { exact: false })).toHaveCount(0);

    // 첫 점은 판정 응답의 확정 고점 목록에서 고른다 — 후보는 모두 그 점에서 시작한다
    await layers.getByRole("radio", { name: "첫 점은 내가 고르기" }).check();
    // 첫 점 목록은 채널 응답(그 방향의 확정 피벗)이다 — 하락 채널이라 고점만 있다
    const firstPoint = layers.getByLabel("첫 점 · 구간 안의 확정 피벗");
    await expect(firstPoint.locator("option").nth(1)).toHaveText(/^고점 /u);
    await firstPoint.selectOption("2024-11-06");
    const candidates = layers.getByRole("radio", { name: /^후보 \d+ · / });
    await expect(candidates.first()).toBeVisible();
    for (const name of await candidates.evaluateAll((items) => items.map((item) => item.parentElement?.textContent ?? "")))
      expect(name).toContain("2024-11-06 →");
    await expect(candidates.first()).toBeChecked();

    // 차트에 추가하면 패널이 닫히고 초점이 "채널" 버튼으로 돌아오며, 자동 후보 칸에 한 줄이 생긴다
    await layers.getByRole("button", { name: "차트에 추가 (새 줄로)" }).click();
    await expect(layers.getByRole("button", { name: "채널", exact: true })).toBeFocused();
    const row = layers.getByRole("listitem").filter({ hasText: "하락 채널" });
    await expect(row).toContainText(`구간 ${FALLING.start}~${FALLING.end} · 그때 판정 하락`);
    const [saved] = await savedLines();
    expect(saved.source).toBe("auto");
    expect(saved.anchors[0].bar_date).toBe("2024-11-06");
    expect(saved.auto).toMatchObject({
      chosen_by: "recommendation",
      window_source: "user",
      regime_window_start: FALLING.start,
      regime_window_end: FALLING.end,
      params: { direction: "down", first_anchor: "2024-11-06" },
    });
  },
);

test(
  "US-CS-17 수평 구간과 되돌림을 만들어 구간별로 켜고 끄고, 근거 패널에서 기준점·생긴 날·레벨을 보고 내 선으로 복사한다",
  { tag: ["@story", "@US-CS-17"] },
  async ({ page }) => {
    await clearLines();
    const chart = await openKospi(page, "week");
    const layers = layersOf(page);

    // 수평 구간: 기본 분석 구간은 화면 기간이고 찾은 구간 표는 서버 결과 그대로다
    await layers.getByRole("button", { name: "수평 구간", exact: true }).click();
    await expect(page.getByRole("heading", { level: 2, name: "수평 지지·저항 구간 만들기" })).toBeFocused();
    const window = { start: await windowFields(page).start.inputValue(), end: await windowFields(page).end.inputValue() };
    const zones = requireData(
      (
        await getChartZones({
          client: apiClient,
          query: { ...KOSPI, timeframe: "week", window_start: window.start, window_end: window.end },
        })
      ).data,
      "zones",
    );
    expect(zones.zones.length).toBeGreaterThan(2);
    const picks = layers.getByRole("checkbox", { name: /구간 추가$/u });
    await expect(picks).toHaveCount(zones.zones.length);
    const [top] = zones.zones;
    await expect(layers.getByRole("rowheader", { name: `${indexPrice(top.low)}–${indexPrice(top.high)}` })).toBeVisible();
    await expect(layers.getByText(zones.basis)).toBeVisible();
    // 묶기·접촉·구분 규칙 문장도 서버 응답 그대로다
    for (const rule of zones.rules) await expect(layers.getByText(rule)).toBeVisible();
    // 한 구간을 빼고 나머지를 추가한다
    await picks.first().uncheck();
    await layers.getByRole("button", { name: "고른 구간 차트에 추가" }).click();
    await expect(layers.getByRole("button", { name: "수평 구간", exact: true })).toBeFocused();
    const kept = zones.zones.length - 1;
    const zoneRow = layers.getByRole("listitem").filter({ hasText: `수평 지지·저항 구간 · ${kept}곳` });
    await expect(zoneRow).toBeVisible();

    // 구간별로 펼쳐 한 구간만 보기 — 나머지는 보이기가 꺼진다(저장 칸)
    await zoneRow.getByRole("button", { name: /하나씩 보기$/u }).click();
    const each = zoneRow.getByRole("listitem");
    await expect(each).toHaveCount(kept);
    await each.nth(1).getByRole("button", { name: /구간만 보기$/u }).click();
    await expect(each.nth(1).getByRole("checkbox")).toBeChecked();
    await expect(each.nth(0).getByRole("checkbox")).not.toBeChecked();
    await expect.poll(async () => (await savedLines()).filter((line) => line.visible).length).toBe(1);

    // 되돌림: 기준 스윙을 고르면 결과 표의 레벨 가격이 서버 응답(이번 주기 레벨 가격)이다
    await layers.getByRole("button", { name: "되돌림", exact: true }).click();
    await layers.getByRole("radio", { name: /^가장 최근 확정 스윙/u }).check();
    const retracementWindow = {
      start: await windowFields(page).start.inputValue(),
      end: await windowFields(page).end.inputValue(),
    };
    const retracement = requireData(
      (
        await getChartRetracement({
          client: apiClient,
          query: {
            ...KOSPI,
            timeframe: "week",
            swing: "latest_confirmed",
            window_start: retracementWindow.start,
            window_end: retracementWindow.end,
          },
        })
      ).data,
      "retracement",
    );
    const drawing = retracement.drawing;
    if (drawing === null || retracement.created === null) throw new Error("practice KOSPI has no confirmed swing");
    const half = drawing.paths.find((path) => path.level === 0.5);
    if (half === undefined) throw new Error("retracement has no 0.5 level");
    await expect(levelRow(page, "0.5")).toContainText(indexPrice(half.end.price));
    // 미리보기는 메인 차트다 — 레벨 라벨 "r (가격)"이 기준 봉 자리에 보인다
    const halfStart = half.start.price.toLocaleString("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    await expect(chart.getByText(`0.5 (${halfStart})`)).toBeVisible();
    await layers.getByRole("button", { name: "차트에 추가 (새 줄로)" }).click();
    const retracementRow = layers.getByRole("listitem").filter({ hasText: "피보나치 되돌림" });
    await expect(retracementRow).toBeVisible();

    // 근거: 기준점(먼저 점 1·나중 점 0), 생긴 날, 이번 주기 레벨, 저장 구조
    await retracementRow.getByRole("button", { name: "피보나치 되돌림 근거" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "피보나치 되돌림" })).toBeFocused();
    await expect(levelRow(page, "먼저 점 (1)")).toContainText(`${drawing.anchors[0].bar_date}${indexPrice(drawing.anchors[0].price)}`);
    await expect(levelRow(page, "나중 점 (0)")).toContainText(`${drawing.anchors[1].bar_date}${indexPrice(drawing.anchors[1].price)}`);
    await expect(layers.getByText(`마지막 기준점이 확정된 ${retracement.created} 봉에 생겼어요.`, { exact: false })).toBeVisible();
    await expect(levelRow(page, "0.5")).toContainText(indexPrice(half.end.price));
    await layers.getByText("저장 구조 보기").click();
    await expect(layers.getByText('"kind": "fib_retracement"', { exact: false })).toBeVisible();

    // 내 선으로 복사: 기준점을 고칠 수 있는 내 선(auto_copied)이 "내가 그은 선"에 생긴다
    await layers.getByRole("button", { name: "내 선으로 복사 (고칠 수 있게)" }).click();
    await expect(layers.getByText("내 선으로 복사했어요.", { exact: false })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(retracementRow.getByRole("button", { name: "피보나치 되돌림 근거" })).toBeFocused();
    await expect(
      layers.getByRole("region", { name: "내가 그은 선" }).getByRole("button", { name: /^되돌림 \S+ → \S+$/u }),
    ).toBeVisible();
    expect((await savedLines()).filter((line) => line.source === "auto_copied")).toHaveLength(1);

    // 수동 그리기 중에는 자동 후보를 가린다
    await page.getByRole("group", { name: "분석 방식" }).getByRole("button", { name: "수동 그리기" }).click();
    await expect(layers.getByText("수동 그리기 중에는 자동 후보를 가려 둬요.", { exact: false })).toBeVisible();
    await expect(retracementRow).toHaveCount(0);
    // 차트에도 자동 선이 없다(내 선으로 복사한 되돌림만 남는다)
    await expect(chart.locator('[class*="chart-line--auto-"]')).toHaveCount(0);
    await expect(chart.locator(".chart-line--mine")).toHaveCount(1);
  },
);

test(
  "US-CS-18 내가 그은 수평선과 자동 되돌림이 겹치는 가격대를 중첩 근거로 보고, 내 선을 빼면 그 중첩이 사라진다",
  { tag: ["@story", "@US-CS-18"] },
  async ({ page }) => {
    await clearLines();
    // 준비: 자동 되돌림 하나(응답 기준점·기록 그대로)와 그 0.5 레벨 가격에 손으로 그은 수평선 하나
    const found = requireData(
      (await getChartRetracement({ client: apiClient, query: { ...KOSPI, timeframe: "week", swing: "range_extremes" } }))
        .data,
      "retracement",
    );
    if (found.drawing === null) throw new Error("practice KOSPI has no confirmed swing");
    const half = found.drawing.paths.find((path) => path.level === 0.5);
    if (half === undefined) throw new Error("retracement has no 0.5 level");
    const line = { ...KOSPI, timeframe: "week" as const };
    requireData(
      (
        await createChartLine({
          client: apiClient,
          body: {
            instrument_kind: line.kind,
            instrument_code: line.code,
            timeframe: line.timeframe,
            kind: "fib_retracement",
            source: "auto",
            anchors: found.drawing.anchors,
            auto: found.record,
          },
        })
      ).data,
      "create retracement",
    );
    requireData(
      (
        await createChartLine({
          client: apiClient,
          body: {
            instrument_kind: line.kind,
            instrument_code: line.code,
            timeframe: line.timeframe,
            kind: "hline",
            source: "manual",
            anchors: [{ bar_date: half.end.bar_date, price: half.end.price }],
          },
        })
      ).data,
      "create hline",
    );

    const chart = await openKospi(page, "week");
    const layers = layersOf(page);
    const confluenceRow = layers.getByRole("listitem").filter({ hasText: /^중첩 구간 · \d+곳/u });
    await expect(confluenceRow).toContainText("내 선 포함");
    await confluenceRow.getByRole("button", { name: "중첩 근거" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "중첩 근거" })).toBeFocused();

    // 내 수평선과 되돌림 0.5가 서로 다른 두 종류로 겹친 가격대 — 교집합·종류·구성 선이 보인다
    const groups = layers.getByRole("region", { name: /^중첩 \d+ · / });
    const group = groups.filter({ hasText: "레벨 0.5" });
    await expect(group).toContainText("수평선·되돌림 (2종)");
    await expect(group).toContainText(`수평선 ${indexPrice(half.end.price)} · 내 선`);
    await expect(group).toContainText("· 자동 · 레벨 0.5");
    await expect(layers.getByText("내 선은 내가 보고 그은 것이라", { exact: false })).toBeVisible();
    await expect(layers.getByText("서로 독립된 증거가 아니에요", { exact: false })).toBeVisible();
    await expect(layers.getByText("서로 다른 종류 2개 이상이 덮는 가격대만 중첩이에요.", { exact: false })).toBeVisible();
    // 차트 오른쪽 잉크 괄호(그림 장식이라 CSS로 센다)
    await expect(chart.locator(".confluence-bracket")).not.toHaveCount(0);

    // 내 선을 빼면 되돌림 혼자 남아 그 가격대는 중첩이 아니다 — 선의 보이기는 그대로다
    await layers.getByRole("checkbox", { name: "내가 그은 선도 중첩 계산에 넣기" }).uncheck();
    await expect(groups.filter({ hasText: "레벨 0.5" })).toHaveCount(0);
    expect((await savedLines()).every((saved) => saved.visible)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(confluenceRow.getByRole("button", { name: "중첩 근거" })).toBeFocused();
    await expect(confluenceRow).toContainText("자동 선만");
  },
);
