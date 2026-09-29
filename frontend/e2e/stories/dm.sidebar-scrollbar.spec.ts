/**
 * 정동민의 스토리 e2e — 페이지 세로 스크롤바가 폭을 차지하는 창에서도 AI 사이드바 자리가 흔들리지 않는다
 * (US-DM-03, #290 리뷰 P1-1).
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
  "US-DM-03 스크롤바가 폭을 차지하는 창에서도 사이드바가 붙었다 떴다 하지 않는다",
  { tag: ["@story", "@US-DM-03"] },
  async ({ page }) => {
    // 1660×1120: 스크롤바가 없을 때 본문은 1424px로 계약과 사이드바를 나란히 두는 임계(1418px) 위다.
    // 사이드바를 붙이면 페이지가 창보다 길어져 스크롤바(15px)가 본문을 1409px로 줄인다. 판정이 스크롤바를
    // 뺀 폭을 읽으면 사이드바가 매 프레임 붙었다 떴다 했다.
    await page.setViewportSize({ width: 1660, height: 1120 });
    await openEditor(page, "/research/strategies/new");
    await page
      .getByRole("button", { name: "AI 어시스턴트", exact: true })
      .click();
    await expect(
      page.getByRole("separator", { name: "AI 어시스턴트 크기 조절" }),
    ).toBeVisible();
    // 스크롤바가 폭을 차지하는 상태를 재현했는지 먼저 본다. 사이드바 내용이 늦게 차서 기다린다.
    await expect
      .poll(() =>
        page.evaluate(
          () => window.innerWidth - document.documentElement.clientWidth,
        ),
      )
      .toBeGreaterThan(0);
    const remounts = await page.evaluate(
      () =>
        new Promise<number>((resolve) => {
          let count = 0;
          const observer = new MutationObserver((records) => {
            for (const record of records)
              for (const node of record.addedNodes)
                if (
                  node instanceof HTMLElement &&
                  (node.matches(".ide__assistant") ||
                    node.querySelector(".ide__assistant") !== null)
                )
                  count += 1;
          });
          observer.observe(document.body, { childList: true, subtree: true });
          setTimeout(() => {
            observer.disconnect();
            resolve(count);
          }, 1_000);
        }),
    );
    expect(remounts, "1초 동안 사이드바가 다시 마운트된 횟수").toBe(0);
  },
);
