/**
 * Playwright spec 들이 함께 쓰는 워크벤치 헬퍼 — 편집기·저장·백테스트 로케이터, 문서 상태 대기,
 * 클립보드로 편집기 원문 읽기, revision URL 해석. `workbench.workflow.spec.ts`(CI 가 도는 릴리스 게이트)와
 * `workbench.real-equity.spec.ts`(opt-in 실데이터)가 같은 접근성 이름·API path 를 보도록 한 곳에 둔다.
 * 접근성 이름이 바뀌면 CI 의 workflow spec 이 먼저 깨지고, 여기서 고치면 real-equity 도 함께 따라온다.
 */
import { expect, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import type { RunEnvironment } from "../src/shared/api/generated";
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

/**
 * 편집기에 넣은 텍스트의 검증(parse·compile)이 끝나기를 기다린다. 부하가 큰 러너에서 검증 결과가
 * 도착하기 전의 중간 상태를 단언이 읽는 경합을 막는다(#240 후보 (a)). 문서 상태 배지의
 * `data-settled` 는 compile 버전이 입력 버전을 따라잡았거나 구문 오류로 compile 이 시작되지 않을 때
 * 참이다(`isDocumentSettled`).
 */
export const waitForSettledDocument = async (page: Page) => {
  const status = page.getByRole("status", { name: "문서 상태" });
  await expect(
    status,
    "문서 검증이 입력 버전을 따라잡는다(#240)",
  ).toHaveAttribute("data-settled", "true");
  return status;
};

export const expectPhase = async (page: Page, phase: string) => {
  const status = await waitForSettledDocument(page);
  await expect(status).toContainText(phase);
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

/**
 * e2e 가 실행 설정 패널에 넣는 기간·유니버스(P3-02). schema 1.2 부터 이 값은 전략 문서 밖에 있고, 실행
 * 설정 스키마가 기본값을 주지 않아 사용자가 정해야 백테스트·추적이 열린다. mock 어댑터는 fixture 달력 밖
 * 세션을 (종목, 날짜)의 함수로 합성하므로 옛 골든(1.1)의 기간을 그대로 쓴다.
 */
export const RUN_ENVIRONMENT = {
  start: "2021-01-01",
  end: "2026-08-31",
  universe_id: "krx.common-stock",
} as const;

/**
 * 실행 설정 패널이 요청에 싣는 `environment`: 값이 없는(null) 선택 칸은 싣지 않는다. 업그레이드 응답의
 * `environment` 처럼 모든 칸을 가진 값을 실행 요청과 비교할 때 쓴다.
 */
export const requestedEnvironment = (
  environment: Readonly<Record<string, unknown>>,
): Record<string, unknown> =>
  Object.fromEntries(
    Object.entries(environment).filter(([, value]) => value !== null),
  );

const RUN_ENVIRONMENT_SCHEMA = JSON.parse(
  readFileSync(
    resolve(
      ownDirectory,
      "../../backend/tests/fixtures/strategy_documents/run-environment-schema.json",
    ),
    "utf8",
  ),
) as { properties: Record<string, { default?: unknown }> };

/**
 * 실행 설정 스키마 기본값(`GET /api/v1/run-environments/schema`)에 기간·유니버스를 채운 요청 본문의
 * `environment`. 칸이 늘어도 기대값을 손으로 고치지 않게 backend 스키마 사본에서 만든다.
 */
export const REQUESTED_ENVIRONMENT = requestedEnvironment({
  ...Object.fromEntries(
    Object.entries(RUN_ENVIRONMENT_SCHEMA.properties).map(([name, node]) => [
      name,
      node.default ?? null,
    ]),
  ),
  ...RUN_ENVIRONMENT,
}) as unknown as RunEnvironment;

/**
 * 실행 설정 패널을 열어 기간·유니버스를 채우고 닫는다. `keyboard` 면 패널을 여닫을 때도 포인터 없이 초점과
 * Enter 만 쓴다(US-SM-04 키보드 스토리). `via: "band"` 는 사용자가 막혔을 때 밟는 길이다 — 요약 띠가
 * 비어 있는 칸 이름을 말하고, 띠의 "실행 설정 채우기"가 패널을 열어 첫 빈 칸(시작일)에 초점을 옮긴다
 * (P3-02 결정 1 보충, US-DM-03·04·08 수용 기준). 세 칸이 모두 비어 있는 상태에서만 쓴다.
 */
export const fillRunEnvironment = async (
  page: Page,
  {
    keyboard = false,
    via = "toggle",
  }: { keyboard?: boolean; via?: "toggle" | "band" } = {},
  environment: {
    start: string;
    end: string;
    universe_id: string;
  } = RUN_ENVIRONMENT,
) => {
  // 실행 설정은 문서 밖이지만, 편집기 검증이 끝나기 전에 패널을 여닫으면 뒤이은 문서 단언이 중간
  // 상태를 읽는다(#240). 먼저 검증을 끝낸다.
  await waitForSettledDocument(page);
  const toggle = page.getByLabel("실행 설정 열기");
  const band = page.getByRole("region", { name: "실행 설정 요약" });
  const fields = [
    [page.getByLabel("시작일", { exact: true }), environment.start],
    [page.getByLabel("종료일", { exact: true }), environment.end],
    [
      page.getByRole("textbox", { name: "유니버스", exact: true }),
      environment.universe_id,
    ],
  ] as const;
  const press = async (target: Locator) => {
    if (keyboard) {
      await target.focus();
      await page.keyboard.press("Enter");
    } else {
      await target.click();
    }
  };
  if (via === "band") {
    await expect(band).toContainText(
      "실행 설정에서 시작일·종료일·유니버스 칸을 채우세요.",
    );
    await press(band.getByRole("button", { name: "실행 설정 채우기" }));
    await expect(fields[0][0]).toBeFocused();
  } else {
    await press(toggle);
  }
  // `fill` 은 포인터를 쓰지 않는다. 날짜 칸을 타자로 채우면 브라우저 locale 의 칸 순서(월/일/연)에 묶인다.
  for (const [field, value] of fields) {
    await field.fill(value);
    await expect(field).toHaveValue(value);
  }
  await expect(band).toContainText(environment.universe_id);
  await press(toggle);
};
