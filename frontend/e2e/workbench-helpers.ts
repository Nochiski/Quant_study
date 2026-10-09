/**
 * Playwright spec 들이 함께 쓰는 워크벤치 헬퍼 — 편집기·저장·백테스트 로케이터, 문서 상태 대기,
 * 클립보드로 편집기 원문 읽기, revision URL 해석. `workbench.workflow.spec.ts`(CI 가 도는 릴리스 게이트)와
 * `workbench.real-equity.spec.ts`(opt-in 실데이터)가 같은 접근성 이름·API path 를 보도록 한 곳에 둔다.
 * 접근성 이름이 바뀌면 CI 의 workflow spec 이 먼저 깨지고, 여기서 고치면 real-equity 도 함께 따라온다.
 */
import { expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createClient } from "../src/shared/api/generated/client";
import { backendOrigin, previewOrigin } from "./ports.mjs";

export const BACKEND = backendOrigin();
export const apiClient = createClient({ baseUrl: BACKEND });
const ownDirectory = dirname(fileURLToPath(import.meta.url));
/** backend 소유 골든 fixture(schema 1.1). frontend 는 읽기만 한다(`frontend-testing.md`). */
export const GOLDEN = readFileSync(
  resolve(
    ownDirectory,
    "../../backend/tests/fixtures/strategy_documents/quality_momentum.yaml",
  ),
  "utf8",
).replace(/\r\n?/gu, "\n");

export const editor = (page: Page) =>
  page.getByRole("textbox", { name: "편집기", exact: true });
export const save = (page: Page) =>
  page.getByRole("button", { name: "리비전 저장", exact: true });
export const validate = (page: Page) =>
  page.getByRole("button", { name: "검증", exact: true });
export const backtest = (page: Page) =>
  page.getByRole("button", { name: "백테스트", exact: true });

export const openEditor = async (page: Page, url: string) => {
  const response = await page.goto(url);
  expect(response?.ok()).toBe(true);
  await expect(editor(page)).toBeVisible();
};

export const replaceSource = async (page: Page, source: string) => {
  await editor(page).fill(source);
};

export const expectPhase = async (page: Page, phase: string) => {
  await expect(page.getByRole("status", { name: "문서 상태" })).toContainText(
    phase,
  );
};

export const requireData = <Value>(
  data: Value | undefined,
  operation: string,
): Value => {
  if (data === undefined) throw new Error(`${operation} returned no data`);
  return data;
};

/**
 * 편집기 원문 전체를 읽는다. 전체 선택 → 복사 → 클립보드 읽기를 **연속 두 번 같은 값이 나올 때까지**
 * 되풀이한다. 탭을 YAML로 바꾸면 선택된 pointer를 편집기에 드러내는 reveal이 비동기로 한 틱 늦게
 * 도착한다(route 테스트 P6-03 주석과 같은 현상). 그 reveal이 Ctrl+A 뒤에 떨어지면 선택이 그 pointer
 * 범위로 바뀌어 원문 대신 조각이 복사된다 — 화면이 무거워진 main 반영 뒤 그래프 되돌리기 e2e가 이
 * 경로로 간헐 실패했다. reveal은 한 번 오고 끝나므로 두 번 연속 같은 값이면 그것이 전체 원문이다.
 */
export const currentSource = async (page: Page) => {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: previewOrigin(),
  });
  const copyAll = async (): Promise<string> => {
    await editor(page).click();
    await editor(page).press("Control+A");
    await editor(page).press("Control+C");
    return page.evaluate(() => navigator.clipboard.readText());
  };
  let previous = await copyAll();
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const next = await copyAll();
    if (next === previous) return next;
    previous = next;
  }
  throw new Error("editor source kept changing while it was being copied");
};

export const strategyIdentity = (page: Page) => {
  const match = new URL(page.url()).pathname.match(
    /^\/research\/strategies\/([^/]+)\/revisions\/(\d+)$/u,
  );
  if (match === null)
    throw new Error(`Not on a strategy revision: ${page.url()}`);
  return { strategyId: match[1]!, revision: Number(match[2]) };
};

export const saveAndWaitForRevision = async (page: Page, revision: number) => {
  await expect(save(page)).toBeEnabled();
  await save(page).click();
  await expect(page).toHaveURL(
    new RegExp(
      `/research/strategies/[^/]+/revisions/${revision}(?:\\?.*)?$`,
      "u",
    ),
  );
  await expectPhase(page, "저장됨");
};

/**
 * 골든 fixture 문자열 치환 — 없는 문자열이면 조용히 원문을 돌려주는 `String.replace` 대신 즉시 실패해,
 * fixture(backend 소유)가 바뀌었을 때 15분짜리 실데이터 실행 끝에서가 아니라 첫 줄에서 알린다.
 */
export const mustReplace = (text: string, from: string, to: string): string => {
  const next = text.replace(from, to);
  if (next === text)
    throw new Error(
      `golden fixture no longer contains ${JSON.stringify(from)}`,
    );
  return next;
};
