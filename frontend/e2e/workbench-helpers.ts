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

export const BACKEND = "http://localhost:8000";
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

export const currentSource = async (page: Page) => {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: "http://localhost:5173",
  });
  await editor(page).click();
  await editor(page).press("Control+A");
  await editor(page).press("Control+C");
  return page.evaluate(() => navigator.clipboard.readText());
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
    throw new Error(`golden fixture no longer contains ${JSON.stringify(from)}`);
  return next;
};
