/**
 * 정동민의 스토리 e2e — 페이지 세로 스크롤바가 폭을 차지하는 창에서도 AI 창 자리가 흔들리지 않는다
 * (US-DM-03, #290 리뷰 P1-1). AI 는 화면 위에 떠 있는 창이라 본문 폭 판정에 끼지 않지만, 스크롤바가 생기고
 * 없어지는 창에서 창이 다시 마운트되거나 위치가 흔들리지 않는지는 계속 본다.
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다. 헤드리스 기본값
 * (`--hide-scrollbars`)은 스크롤바를 숨기므로, 이 파일만 Windows 기본처럼 스크롤바가 폭을 차지하는 창을
 * 띄운다(브라우저 실행 옵션이라 파일 단위로만 바꿀 수 있다).
 */
import { expect, test } from "@playwright/test";


import { openEditor } from "../workbench-helpers";

test.use({
  launchOptions: {
    args: ["--lang=ko-KR"],
    ignoreDefaultArgs: ["--hide-scrollbars"],
  },
});

test(
  "US-DM-03 스크롤바가 폭을 차지하는 창에서도 AI 창이 흔들리지 않는다",
  { tag: ["@story", "@US-DM-03"] },
  async ({ page }) => {
    // 새 본문 여백에서 1636×1120은 스크롤바를 제외한 폭이 1424px로 나란히 배치하는 임계(1418px) 위다.
    // OS의 실제 classic gutter가 차지한 폭까지 판정에서 빼면 임계 아래가 된다.
    await page.setViewportSize({ width: 1636, height: 1120 });
    await openEditor(page, "/research/strategies/new");
    // 본문은 창 높이에 제한되므로, 상시 페이지 스크롤바를 쓰는 OS 환경을 fixture에서 명시한다.
    await page.addStyleTag({ content: ":root { overflow-y: scroll; }" });
    // 전략 구조·계약을 먼저 열고 AI 창을 마지막에 연다(AI 창은 최소화한 채 시작한다, PR #624 결정).
    await page.getByRole("button", { name: "전략 구조", exact: true }).click();
    await page.getByRole("button", { name: "계약", exact: true }).click();
    await page
      .getByRole("button", { name: "AI 어시스턴트", exact: true })
      .click();
    await expect(
      page.getByRole("complementary", { name: "AI 어시스턴트", exact: true }),
    ).toBeVisible();
    // 가짜 폭을 넣지 않는다. OS의 실제 스크롤바가 폭을 차지하는 상태인지 먼저 본다.
    await expect
      .poll(() =>
        page.evaluate(
          () => window.innerWidth - document.documentElement.clientWidth,
        ),
      )
      .toBeGreaterThan(0);
    const observed = await page.getByRole("complementary", { name: "AI 어시스턴트", exact: true }).evaluate(
      (sidebar) =>
        new Promise<{ remounts: number; samples: number; maxXDelta: number; maxWidthDelta: number }>((resolve) => {
          let remounts = 0;
          let samples = 0;
          let maxXDelta = 0;
          let maxWidthDelta = 0;
          const initial = sidebar.getBoundingClientRect();
          let frame = 0;
          const measure = () => {
            const current = sidebar.getBoundingClientRect();
            samples += 1;
            maxXDelta = Math.max(maxXDelta, Math.abs(current.x - initial.x));
            maxWidthDelta = Math.max(maxWidthDelta, Math.abs(current.width - initial.width));
            frame = requestAnimationFrame(measure);
          };
          const observeChanges = (records: MutationRecord[]) => {
            for (const record of records)
              for (const node of record.addedNodes)
                if (
                  node instanceof HTMLElement &&
                  (node.matches(".floating-window") ||
                    node.querySelector(".floating-window") !== null)
                )
                  remounts += 1;
          };
          const observer = new MutationObserver(observeChanges);
          observer.observe(document.body, { childList: true, subtree: true });
          frame = requestAnimationFrame(measure);
          setTimeout(() => {
            cancelAnimationFrame(frame);
            observeChanges(observer.takeRecords());
            observer.disconnect();
            resolve({ remounts, samples, maxXDelta, maxWidthDelta });
          }, 1_000);
        }),
    );
    expect(observed.remounts, "1초 동안 AI 창이 다시 마운트된 횟수").toBe(0);
    expect(observed.samples, "같은 1초 동안 실제 배치를 여러 프레임 관측한다").toBeGreaterThan(1);
    // 소수점 배치 반올림만 0.5 CSS px까지 허용한다. 붙음·뜸 전환의 수십 px 재배치는 이 한계를 넘는다.
    expect(observed.maxXDelta, "같은 1초 동안 AI 창 가로 위치의 최대 편차").toBeLessThanOrEqual(0.5);
    expect(observed.maxWidthDelta, "같은 1초 동안 AI 창 폭의 최대 편차").toBeLessThanOrEqual(0.5);
  },
);
