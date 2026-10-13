/**
 * 편집기 helper — 편집기 로케이터, 원문 읽기·바꾸기, 문서 검증 대기. spec 은 `workbench-helpers.ts`가 다시
 * 내보낸 것을 쓰고, 매뉴얼 촬영 스크립트(`scripts/capture-manual-screenshots.mjs`)는 node 의 type stripping
 * 으로 이 파일을 바로 import 한다. 그래서 여기에는 node 가 그대로 푸는 import 만 둔다 — 타입은 `type`
 * 표기로만 가져오고, 확장자 없이 부르는 로컬 모듈(생성 SDK 등)을 import 하지 않는다.
 */
import { expect, type Page } from "@playwright/test";

export const editor = (page: Page) =>
  page.getByRole("textbox", { name: "편집기", exact: true });

/**
 * 편집기 원문 전체를 편집기가 그린 줄에서 읽는다. 선택·포커스·URL을 건드리지 않으므로, 탭을 바꾼 뒤
 * 늦게 오는 pointer reveal이 선택을 옮겨도 읽는 값이 흔들리지 않는다. 줄은 편집기처럼 `\n`으로 잇는다.
 * CodeMirror는 화면에서 먼 줄을 그리지 않고 자리(`.cm-gap`)만 두므로, 그런 자리가 있으면 원문 전체를
 * 읽을 수 없다고 멈춘다.
 */
export const currentSource = (page: Page): Promise<string> =>
  editor(page).evaluate((content) => {
    if (content.querySelector(".cm-gap") !== null)
      throw new Error("the editor did not render every line of the source");
    return Array.from(
      content.querySelectorAll(":scope > .cm-line"),
      (line) => line.textContent ?? "",
    ).join("\n");
  });

/**
 * 편집기 원문 전체를 `source`로 바꾸고, 정확히 그 텍스트가 남았는지 확인한다(#240).
 *
 * 전체 선택은 편집기 자신의 명령(`ControlOrMeta+A`, CodeMirror `Mod-a`)으로 한다. macOS의 `Control+A`는
 * 줄 처음 이동이다. Playwright `fill`은 contenteditable에서 DOM 선택으로 전체를 고르는데, CodeMirror가
 * 그 DOM 선택을 읽기 전에 편집기 갱신(화면이 뜬 직후 파싱·compile·스키마 적재가 넣는 진단·확장)이 오면
 * 편집기 상태의 선택(문서 처음의 커서)으로 되돌려 쓴다. 그러면 넣은 텍스트가 원래 문서 앞에 붙어
 * "구문 오류"가 된다(부하 속 새 전략 화면에서 재현했다).
 */
export const replaceSource = async (page: Page, source: string) => {
  await editor(page).press("ControlOrMeta+A");
  await page.keyboard.insertText(source);
  await expect
    .poll(() => currentSource(page), {
      message: "편집기 원문이 넣은 텍스트와 같다(#240)",
    })
    .toBe(source);
};

/**
 * 편집기에 넣은 텍스트의 검증(parse·compile)이 끝나기를 기다린다. 부하가 큰 러너에서 검증 결과가
 * 도착하기 전의 중간 상태를 단언이 읽지 않게 한다. 문서 상태 배지의 `data-settled` 는 compile 버전이
 * 입력 버전을 따라잡았거나 구문 오류로 compile 이 시작되지 않을 때 참이다(`isDocumentSettled`).
 * #240 을 좇으며 넣었지만 #240 의 원인은 이 경합이 아니라 `fill` 의 DOM 선택을 CodeMirror 갱신이
 * 편집기 상태의 선택으로 되쓴 것이었다(`replaceSource`).
 */
export const waitForSettledDocument = async (page: Page) => {
  const status = page.getByRole("status", { name: "문서 상태" });
  await expect(status, "문서 검증이 입력 버전을 따라잡는다").toHaveAttribute(
    "data-settled",
    "true",
  );
  return status;
};

export const expectPhase = async (page: Page, phase: string) => {
  const status = await waitForSettledDocument(page);
  await expect(status).toContainText(phase);
};
