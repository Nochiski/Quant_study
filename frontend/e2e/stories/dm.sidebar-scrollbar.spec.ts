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
    // 새 본문 여백에서 1636×1120은 스크롤바를 제외한 폭이 1424px로 나란히 배치하는 임계(1418px) 위다.
    // OS의 실제 classic gutter가 차지한 폭까지 판정에서 빼면 임계 아래가 된다.
    await page.setViewportSize({ width: 1636, height: 1120 });
    await openEditor(page, "/research/strategies/new");
    // 본문은 창 높이에 제한되므로, 상시 페이지 스크롤바를 쓰는 OS 환경을 fixture에서 명시한다.
    await page.addStyleTag({ content: ":root { overflow-y: scroll; }" });
    await page.getByRole("button", { name: "전략 구조", exact: true }).click();
    await page.getByRole("button", { name: "계약", exact: true }).click();
    await page
      .getByRole("button", { name: "AI 어시스턴트", exact: true })
      .click();
    await expect(
      page.getByRole("separator", { name: "AI 어시스턴트 크기 조절" }),
    ).toBeVisible();
    // 가짜 폭을 넣지 않는다. OS의 실제 스크롤바가 폭을 차지하는 상태인지 먼저 본다.
    await expect
      .poll(() =>
        page.evaluate(
          () => window.innerWidth - document.documentElement.clientWidth,
        ),
      )
      .toBeGreaterThan(0);
    const observed = await page.evaluate(
      () =>
        new Promise<{ remounts: number; modeTransitions: number }>((resolve) => {
          let remounts = 0;
          let modeTransitions = 0;
          const floating = (classes: string | null) => classes?.split(/\s+/u).includes("ide__drawer") ?? false;
          const observeChanges = (records: MutationRecord[]) => {
            for (const record of records)
              for (const node of record.addedNodes)
                if (
                  node instanceof HTMLElement &&
                  (node.matches(".ide__assistant") ||
                    node.querySelector(".ide__assistant") !== null)
                )
                  remounts += 1;
            const modeRecords = records.filter(record => record.type === "attributes" && record.target instanceof HTMLElement && record.target.matches(".ide__assistant"));
            for (const [index, record] of modeRecords.entries()) {
              // 한 callback 안에서 붙음→뜸→붙음이 모두 일어나도 oldValue의 연결로 각 전환을 센다.
              const next = modeRecords.slice(index + 1).find(candidate => candidate.target === record.target);
              const after = next === undefined ? (record.target as HTMLElement).getAttribute("class") : next.oldValue;
              if (floating(record.oldValue) !== floating(after)) modeTransitions += 1;
            }
          };
          const observer = new MutationObserver(observeChanges);
          observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"], attributeOldValue: true });
          setTimeout(() => {
            observeChanges(observer.takeRecords());
            observer.disconnect();
            resolve({ remounts, modeTransitions });
          }, 1_000);
        }),
    );
    expect(observed.remounts, "1초 동안 사이드바가 다시 마운트된 횟수").toBe(0);
    expect(observed.modeTransitions, "같은 1초 동안 사이드바의 붙음·뜸 배치가 바뀐 횟수").toBe(0);
  },
);
