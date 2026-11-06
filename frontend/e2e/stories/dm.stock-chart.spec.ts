/**
 * 정동민의 스토리 e2e — 개별종목차트에 선을 긋고 다시 열어도 남아 있다(US-DM-19).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다. 릴리스 게이트는 연습용(mock) 원천으로
 * 돈다 — 대상 코드가 `practice:` 접두라 같은 연구 DB의 실데이터 선과 섞이지 않는다. 레벨 가격·기하는 backend 테스트가
 * 보고, 여기서는 화면 흐름(긋기 → 저장 → 다시 열기 → 지우고 되돌리기 → 좌표로 되돌림)을 본다.
 */
import { expect, test } from "@playwright/test";

test(
  "US-DM-19 코스피 차트에 마우스로 수평선을 긋고 다시 열어도 남아 있으며, 지운 선을 되돌리고 좌표로 되돌림을 더한다",
  { tag: ["@story", "@US-DM-19"] },
  async ({ page }) => {
    await page.goto("/");
    await page.getByRole("navigation", { name: "주 메뉴" }).getByRole("link", { name: "개별종목차트" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "개별종목차트" })).toBeVisible();

    // 종목 고르기: 검색 칸 → 결과 버튼
    await page.getByRole("searchbox", { name: "종목" }).fill("코스피");
    await page.getByRole("button", { name: /^코스피 .*지수$/u }).click();
    const chart = page.getByRole("group", { name: "코스피 주봉 캔들 차트와 선" });
    await expect(chart).toBeVisible();
    // 연습용 데이터 안내는 서버 문장 그대로다
    await expect(page.getByText("연습용 데이터예요.", { exact: false })).toBeVisible();

    // 수평선: 도구를 고르고 차트를 한 번 누른다
    await page.getByRole("button", { name: "수평 지지·저항선" }).click();
    const box = await chart.boundingBox();
    if (box === null) throw new Error("chart has no bounding box");
    await page.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.3);
    await page.mouse.click(box.x + box.width * 0.45, box.y + box.height * 0.3);

    const layers = page.getByRole("complementary", { name: "레이어" });
    const hline = layers.getByRole("button", { name: /^수평선 [\d,.]+$/u });
    await expect(hline).toHaveCount(1);
    const status = page.getByRole("status").filter({ hasText: "코스피에 그린 선" });
    await expect(status).toHaveText("코스피에 그린 선 1개 · 자동 저장됨");
    const hlineName = (await hline.textContent()) ?? "";

    // 새로고침해도, 다른 화면에 갔다 뒤로 와도 남아 있다
    await page.reload();
    await expect(layers.getByRole("button", { name: hlineName, exact: true })).toBeVisible();
    await page.getByRole("navigation", { name: "주 메뉴" }).getByRole("link", { name: "홈" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "홈" })).toBeVisible();
    await page.goBack();
    await expect(chart).toBeVisible();
    await expect(layers.getByRole("button", { name: hlineName, exact: true })).toBeVisible();

    // 지우면 되돌리기 알림이 뜨고, Ctrl+Z가 같은 선을 되살린다
    await layers.getByRole("button", { name: `${hlineName} 지우기` }).click();
    await expect(page.getByText(`선을 지웠어요: ${hlineName}`)).toBeVisible();
    await expect(layers.getByText("아직 그은 선이 없어요.", { exact: false })).toBeVisible();
    await page.keyboard.press("Control+z");
    await expect(page.getByText("지운 선을 되살렸어요.")).toBeVisible();
    await expect(layers.getByRole("button", { name: hlineName, exact: true })).toBeVisible();
    await expect(status).toHaveText("코스피에 그린 선 1개 · 자동 저장됨");

    // 좌표로 되돌림 추가 — 키보드만 쓴다
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

    await expect(layers.getByRole("button", { name: "되돌림 2024-03-04 → 2024-09-02", exact: true })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(layers.getByRole("heading", { name: "고른 선 · 되돌림" })).toBeVisible();
    // 레벨은 서버 기본(되돌림 0~1)이 그려지고 라벨은 "r (가격)"이다
    await expect(layers.getByRole("checkbox", { name: "0.618" })).toBeChecked();
    await expect(chart.getByText(/^0\.618 \([\d,.]+\)$/u)).toBeVisible();
    await expect(status).toHaveText("코스피에 그린 선 2개 · 자동 저장됨");
  },
);
