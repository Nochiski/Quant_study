/**
 * 정동민의 스토리 e2e — 개별종목차트에 선을 긋고 다시 열어도 남아 있다(US-DM-19).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다. 릴리스 게이트는 연습용(mock) 원천으로
 * 돈다 — 대상 코드가 `practice:` 접두라 같은 연구 DB의 실데이터 선과 섞이지 않는다. 레벨 가격·기하는 backend 테스트가
 * 보고, 여기서는 화면 흐름을 본다. 테스트마다 시작할 때 API로 그 종목의 선을 지워 다시 돌려도 같은 상태에서 시작한다.
 */
import { expect, test, type Locator, type Page } from "@playwright/test";

import {
  deleteChartLine,
  getChartCandles,
  listChartLines,
} from "../../src/shared/api/generated";
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

/** 코스피 주봉 차트를 연다(내비 → 종목 검색 → 결과). 고르면 초점이 차트 제목으로 간다. */
const openKospi = async (page: Page): Promise<Locator> => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "주 메뉴" }).getByRole("link", { name: "개별종목차트" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "개별종목차트" })).toBeVisible();
  await page.getByRole("searchbox", { name: "종목" }).fill("코스피");
  await page.getByRole("button", { name: /^코스피 .*지수$/u }).click();
  await expect(page.getByRole("heading", { level: 2, name: "코스피 · 주봉" })).toBeFocused();
  const chart = page.getByRole("group", { name: "코스피 주봉 캔들 차트와 선" });
  await expect(chart).toBeVisible();
  return chart;
};

/** 그림 칸 안의 비율 자리를 누른다(오른쪽 가격 축 84px·위 12px·아래 날짜 축 28px를 뺀 칸). */
const plotPoint = async (chart: Locator, fx: number, fy: number) => {
  const box = await chart.boundingBox();
  if (box === null) throw new Error("chart has no bounding box");
  return { x: box.x + (box.width - 84) * fx, y: box.y + 12 + (box.height - 40) * fy };
};

const clickPlot = async (page: Page, chart: Locator, fx: number, fy: number) => {
  const at = await plotPoint(chart, fx, fy);
  await page.mouse.move(at.x, at.y);
  await page.mouse.click(at.x, at.y);
};

const priceOf = (title: string) => Number(title.replace(/^수평선 /u, "").replaceAll(",", ""));

test(
  "US-DM-19 코스피 차트에 마우스로 수평선을 긋고 다시 열어도 남아 있으며, 지운 선을 되돌리고 좌표로 되돌림을 더한다",
  { tag: ["@story", "@US-DM-19"] },
  async ({ page }) => {
    await clearLines();
    const chart = await openKospi(page);
    // 연습용 데이터 안내는 서버 문장 그대로다
    await expect(page.getByText("연습용 데이터예요.", { exact: false })).toBeVisible();

    // 조건은 주소가 갖는다: 기간·주기를 바꾸고 뒤로 가면 앞 조건의 버튼이 다시 눌린다
    const conditions = page.getByRole("group", { name: "차트 조건" });
    await conditions.getByRole("button", { name: "1년" }).click();
    await conditions.getByRole("button", { name: "일봉" }).click();
    await expect(conditions.getByRole("button", { name: "일봉" })).toHaveAttribute("aria-pressed", "true");
    await page.goBack();
    await expect(conditions.getByRole("button", { name: "주봉" })).toHaveAttribute("aria-pressed", "true");
    await expect(conditions.getByRole("button", { name: "1년" })).toHaveAttribute("aria-pressed", "true");
    await page.goBack();
    await expect(conditions.getByRole("button", { name: "2년" })).toHaveAttribute("aria-pressed", "true");

    // 수평선: 도구를 고르고 차트를 한 번 누른다
    await page.getByRole("button", { name: "수평 지지·저항선" }).click();
    await clickPlot(page, chart, 0.45, 0.3);
    const layers = page.getByRole("complementary", { name: "레이어" });
    const hline = layers.getByRole("button", { name: /^수평선 [\d,.]+$/u });
    await expect(hline).toHaveCount(1);
    const status = page.getByRole("status").filter({ hasText: "코스피에 그린 선" });
    await expect(status).toHaveText("코스피에 그린 선 1개 · 자동 저장됨");

    // 마그넷: 저장된 가격은 그 봉의 시·고·저·종가 중 하나와 정확히 같다(카드가 붙인 값과 봉 날짜를 보인다)
    const card = layers.getByRole("region", { name: "고른 선 · 수평선" });
    await expect(card).toBeVisible();
    const anchor = card.getByRole("group", { name: "기준점" });
    const barDate = await anchor.getByLabel("봉(날짜)").inputValue();
    const field = (await anchor.getByLabel("붙일 값").inputValue()) as "open" | "high" | "low" | "close";
    const candles = requireData(
      (await getChartCandles({ client: apiClient, query: { ...KOSPI, timeframe: "week" } })).data,
      "candles",
    ).candles;
    const bar = candles.find((candle) => candle.start === barDate);
    expect(bar).toBeDefined();
    await expect(anchor.getByLabel("가격")).toHaveValue(bar![field].toFixed(2));
    const drawn = (await hline.textContent()) ?? "";
    expect(priceOf(drawn)).toBe(Number(bar![field].toFixed(2)));

    // 새로고침해도, 다른 화면에 갔다 뒤로 와도 남아 있다
    await page.reload();
    await expect(layers.getByRole("button", { name: drawn, exact: true })).toBeVisible();
    await page.getByRole("navigation", { name: "주 메뉴" }).getByRole("link", { name: "홈" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "홈" })).toBeVisible();
    await page.goBack();
    await expect(chart).toBeVisible();

    // 키보드: ↑ 한 번 = 한 호가(지수 0.01), Delete = 지우기
    const name = layers.getByRole("button", { name: drawn, exact: true });
    await name.focus();
    await page.keyboard.press("ArrowUp");
    const raised = (priceOf(drawn) + 0.01).toLocaleString("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const raisedName = `수평선 ${raised}`;
    await expect(layers.getByRole("button", { name: raisedName, exact: true })).toBeVisible();
    await layers.getByRole("button", { name: raisedName, exact: true }).press("Delete");
    await expect(page.getByText(`선을 지웠어요: ${raisedName}`)).toBeVisible();
    await expect(layers.getByText("아직 그은 선이 없어요.", { exact: false })).toBeVisible();
    // 되돌리기 버튼
    await page.getByRole("button", { name: "되돌리기 (Ctrl+Z)" }).click();
    await expect(page.getByText("지운 선을 되살렸어요.")).toBeVisible();
    await expect(layers.getByRole("button", { name: raisedName, exact: true })).toBeVisible();
    // Ctrl+Z
    await layers.getByRole("button", { name: `${raisedName} 지우기` }).click();
    await expect(layers.getByText("아직 그은 선이 없어요.", { exact: false })).toBeVisible();
    await page.keyboard.press("Control+z");
    await expect(layers.getByRole("button", { name: raisedName, exact: true })).toBeVisible();
    await expect(status).toHaveText("코스피에 그린 선 1개 · 자동 저장됨");

    // 좌표로 되돌림 추가 — 키보드만 쓴다. 더하면 새 선 이름으로 초점이 간다.
    await layers.getByRole("button", { name: "좌표로 추가" }).focus();
    await page.keyboard.press("Enter");
    const form = layers.getByRole("form", { name: "좌표로 추가" });
    await form.getByLabel("도구").selectOption("fib_retracement");
    await form.getByRole("group", { name: "먼저 점 (1)" }).getByLabel("봉(날짜)").selectOption("2024-03-04");
    await form.getByRole("group", { name: "먼저 점 (1)" }).getByLabel("붙일 값").selectOption("high");
    await form.getByRole("group", { name: "나중 점 (0)" }).getByLabel("봉(날짜)").selectOption("2024-09-02");
    await form.getByRole("group", { name: "나중 점 (0)" }).getByLabel("붙일 값").selectOption("low");
    await form.getByRole("button", { name: "추가" }).focus();
    await page.keyboard.press("Enter");

    const added = layers.getByRole("button", { name: "되돌림 2024-03-04 → 2024-09-02", exact: true });
    await expect(added).toBeFocused();
    await expect(added).toHaveAttribute("aria-pressed", "true");
    // 레벨은 서버 기본(되돌림 0~1)이 그려지고 라벨은 "r (가격)"이다
    await expect(layers.getByRole("checkbox", { name: "0.618" })).toBeChecked();
    await expect(chart.getByText(/^0\.618 \([\d,.]+\)$/u)).toBeVisible();
    await expect(status).toHaveText("코스피에 그린 선 2개 · 자동 저장됨");
  },
);

test(
  "US-DM-19 마우스로 되돌림·채널을 긋고 카드에서 메모·잠금·레벨·연장을 고치며, 손잡이를 끌어 옮기고 다른 주기에서는 손잡이가 없다",
  { tag: ["@story", "@US-DM-19"] },
  async ({ page }) => {
    await clearLines();
    const chart = await openKospi(page);
    const layers = page.getByRole("complementary", { name: "레이어" });

    // 되돌림: 두 점(먼저 = 레벨 1, 나중 = 레벨 0)
    await page.getByRole("button", { name: "피보나치 되돌림" }).click();
    await clickPlot(page, chart, 0.3, 0.2);
    await clickPlot(page, chart, 0.7, 0.8);
    const retracement = layers.getByRole("button", { name: /^되돌림 \S+ → \S+$/u });
    await expect(retracement).toHaveAttribute("aria-pressed", "true");
    await expect(chart.getByText(/^0\.382 \([\d,.]+\)$/u)).toBeVisible();

    // 채널: 기준선 두 점 + 폭 기준점. 채널 기본 레벨은 1.618 확장을 포함한다
    await page.getByRole("button", { name: "피보나치 채널" }).click();
    await clickPlot(page, chart, 0.15, 0.6);
    await clickPlot(page, chart, 0.55, 0.4);
    await clickPlot(page, chart, 0.35, 0.1);
    await expect(layers.getByRole("button", { name: /^채널 \S+부터$/u })).toHaveAttribute("aria-pressed", "true");
    await expect(chart.getByText(/^1\.618 \([\d,.-]+\)$/u)).toBeVisible();

    // 카드: 메모를 넣고 곧바로 잠금을 눌러도 둘 다 남는다(칸 전체 PUT이 차례대로 간다), 레벨·연장 끄기
    await retracement.click();
    const card = layers.getByRole("region", { name: "고른 선 · 되돌림" });
    await card.getByLabel("메모").fill("첫 되돌림");
    await card.getByLabel("메모").press("Enter");
    // 체크 칸은 서버 값을 비추는 제어 칸이라 누른 뒤 캐시가 바뀌면 켜진다(check()의 즉시 확인 대신 기다린다)
    await card.getByLabel("잠금 (끌어도 안 움직임)").click();
    await expect(card.getByLabel("잠금 (끌어도 안 움직임)")).toBeChecked();
    // 레벨 칸은 서버가 그 레벨을 빼고 다시 그린 뒤에 꺼진다(그릴 점이 응답이다)
    await card.getByRole("checkbox", { name: "0.236" }).click();
    await expect(card.getByRole("checkbox", { name: "0.236" })).not.toBeChecked();
    await card.getByLabel("오른쪽 끝까지 연장").click();
    await expect(card.getByLabel("오른쪽 끝까지 연장")).not.toBeChecked();
    await expect(page.getByRole("status").filter({ hasText: "코스피에 그린 선" })).toHaveText(
      "코스피에 그린 선 2개 · 자동 저장됨",
    );
    await page.reload();
    await retracement.click();
    await expect(card.getByLabel("메모")).toHaveValue("첫 되돌림");
    await expect(card.getByLabel("잠금 (끌어도 안 움직임)")).toBeChecked();
    await expect(card.getByRole("checkbox", { name: "0.236" })).not.toBeChecked();
    await expect(card.getByRole("checkbox", { name: "0.5" })).toBeChecked();
    await expect(card.getByLabel("오른쪽 끝까지 연장")).not.toBeChecked();
    // 0.236 라벨은 이제 채널의 것 하나뿐이다(되돌림의 0.236은 꺼졌다)
    await expect(chart.getByText(/^0\.236 \(/u)).toHaveCount(1);
    // 잠긴 선은 손잡이가 없고 카드가 이유를 말한다(손잡이는 포인터 전용 장식이라 CSS로 센다)
    await expect(card.getByText("잠긴 선은 기준점을 고칠 수 없어요.", { exact: false })).toBeVisible();
    await expect(chart.locator(".drawing-surface__handle")).toHaveCount(0);
    await card.getByLabel("잠금 (끌어도 안 움직임)").click();
    await expect(card.getByLabel("잠금 (끌어도 안 움직임)")).not.toBeChecked();
    await expect(chart.locator(".drawing-surface__handle")).toHaveCount(2);

    // 손잡이 끌기: 나중 점을 오른쪽으로 끌어 놓으면 그 자리로 저장된다
    const before = (await retracement.textContent()) ?? "";
    const handle = chart.locator(".drawing-surface__handle").nth(1);
    const box = await handle.boundingBox();
    if (box === null) throw new Error("handle has no bounding box");
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 - 80, box.y + box.height / 2, { steps: 6 });
    await page.mouse.up();
    await expect(retracement).not.toHaveText(before);

    // 다른 주기(일봉)에서는 주봉으로 그린 선의 손잡이가 없고 카드가 이유를 말한다
    await page.getByRole("group", { name: "차트 조건" }).getByRole("button", { name: "일봉" }).click();
    await layers.getByRole("button", { name: /^되돌림 .* · 주봉$/u }).click();
    await expect(
      layers.getByText("주봉에서 그린 선이에요. 기준점은 주봉 화면에서 고쳐요."),
    ).toBeVisible();
    await expect(page.getByRole("group", { name: "코스피 일봉 캔들 차트와 선" }).locator(".drawing-surface__handle")).toHaveCount(0);
  },
);
