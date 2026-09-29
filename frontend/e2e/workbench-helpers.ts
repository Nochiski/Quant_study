/**
 * Playwright spec 들이 함께 쓰는 워크벤치 헬퍼 — 저장·백테스트 로케이터, revision URL 해석, 실행 설정
 * 칸과 채우기, 은퇴 버전 업그레이드 배너. 편집기 로케이터·원문 읽기·바꾸기·문서 검증 대기는 매뉴얼 촬영
 * 스크립트도 쓰므로 `editor-helpers.ts`에 두고 여기서 다시 내보낸다. `workbench.workflow.spec.ts`(CI 가 도는 릴리스 게이트)와
 * `workbench.real-equity.spec.ts`(opt-in 실데이터)가 같은 접근성 이름·API path 를 보도록 한 곳에 둔다.
 * 접근성 이름이 바뀌면 CI 의 workflow spec 이 먼저 깨지고, 여기서 고치면 real-equity 도 함께 따라온다.
 */
import { expect, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import type {
  RunEnvironment,
  UpgradedDocument,
} from "../src/shared/api/generated";
import { createClient } from "../src/shared/api/generated/client";
import { editor, expectPhase, waitForSettledDocument } from "./editor-helpers";
import { backendOrigin } from "./ports.mjs";

export {
  currentSource,
  editor,
  expectPhase,
  replaceSource,
} from "./editor-helpers";

export const BACKEND = backendOrigin();
export const apiClient = createClient({ baseUrl: BACKEND });
const ownDirectory = dirname(fileURLToPath(import.meta.url));
/** backend 소유 골든 fixture(현재 schema 버전). frontend 는 읽기만 한다(`frontend-testing.md`). */
export const GOLDEN = readFileSync(
  resolve(
    ownDirectory,
    "../../backend/tests/fixtures/strategy_documents/quality_momentum.yaml",
  ),
  "utf8",
).replace(/\r\n?/gu, "\n");

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

/**
 * 요소의 가운데를 찍었을 때 맞는 요소가 그 요소(또는 그 안의 글자)가 아니면 무엇인지 돌려준다. 보이는데
 * 옆 칸·겹쳐 뜬 패널·상단 바에 깔려 눌리지도 읽히지도 않는 경우를 잡는다 — Playwright 가시성 검사는 겹침을
 * 보지 않는다(#269). 지금 화면 그대로 찍고 굴리지 않는다. 재기 전에 굴리면 패널을 펼칠 때 포커스가 페이지를
 * 굴려 서랍 머리 줄이 상단 바 밑에 깔린 것을 되돌려 버려 못 봤다(#290 리뷰 r3 P2-1). 창 밖이면 "화면 밖"이다
 * — 사용자가 그 요소를 보는 자리에 페이지를 두는 것은 부르는 쪽이 정한다(`scrollPageTo`).
 */
export const coveringElement = (target: Locator) =>
  target.evaluate((element) => {
    const box = element.getBoundingClientRect();
    const hit = document.elementFromPoint(
      box.x + box.width / 2,
      box.y + box.height / 2,
    );
    if (hit !== null && element.contains(hit)) return null;
    return hit === null
      ? "화면 밖"
      : `${hit.tagName.toLowerCase()} "${(hit.textContent ?? "").trim().slice(0, 40)}"`;
  });

/**
 * 사용자가 요소를 보려고 페이지를 굴린 자리에 둔다 — 요소가 창 세로 가운데에 온다. 옆 칸·겹쳐 뜬 패널과의
 * 겹침(`coveringElement`)을 상단 바와 무관하게 볼 때 쓴다. 굴리는 것은 페이지뿐이다. `scrollIntoView`는 안쪽
 * 스크롤 칸(탭 목록 등)까지 굴려 그 칸 밖으로 밀려 가려진 요소를 드러내 버린다(#296).
 */
export const scrollPageTo = (target: Locator) =>
  target.evaluate((element) => {
    const box = element.getBoundingClientRect();
    window.scrollBy({
      top: box.y + box.height / 2 - window.innerHeight / 2,
      behavior: "instant",
    });
  });

export const requireData = <Value>(
  data: Value | undefined,
  operation: string,
): Value => {
  if (data === undefined) throw new Error(`${operation} returned no data`);
  return data;
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

/** 은퇴 버전 문서의 안내 배너. 문구는 버전 중립이다 — 어느 버전이 은퇴했는지는 backend 가 판정한다. */
export const upgradeBanner = (page: Page) =>
  page.getByRole("region", { name: "이전 schema 문서" });

export const upgradeButton = (page: Page) =>
  upgradeBanner(page).getByRole("button", { name: "현재 버전으로 업그레이드" });

/** 배너의 업그레이드 버튼을 눌러 backend 응답을 돌려준다. */
export const upgradeFromBanner = async (
  page: Page,
): Promise<UpgradedDocument> => {
  const upgrade = upgradeButton(page);
  await expect(upgrade).toBeEnabled();
  const upgraded = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/strategy-documents/upgrade",
  );
  await upgrade.click();
  const response = await upgraded;
  expect(response.status()).toBe(200);
  return (await response.json()) as UpgradedDocument;
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
 * 세션을 (종목, 날짜)의 함수로 합성하므로 은퇴한 1.1 골든(`quality_momentum.v1_1.yaml`)의 `data` 기간·
 * 유니버스를 그대로 쓴다.
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

/** 실행 설정 패널을 여닫는 툴바 토글과 e2e 가 값을 넣고 읽는 패널 칸. */
export const runSettingsInputs = (page: Page) => ({
  toggle: page.getByLabel("실행 설정 열기"),
  start: page.getByLabel("시작일", { exact: true }),
  end: page.getByLabel("종료일", { exact: true }),
  universe: page.getByRole("textbox", { name: "유니버스", exact: true }),
  fee: page.getByRole("spinbutton", { name: "수수료 (bp)" }),
});

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
  // 문서 검증이 끝난 뒤 실행 설정을 채운다. #240 을 좇으며 넣은 순서지만 #240 의 원인은 이 순서가 아니라
  // `fill` 의 DOM 선택을 CodeMirror 갱신이 되쓴 것이었다(`replaceSource`).
  await waitForSettledDocument(page);
  const { toggle, start, end, universe } = runSettingsInputs(page);
  const band = page.getByRole("region", { name: "실행 설정 요약" });
  const fields = [
    [start, environment.start],
    [end, environment.end],
    [universe, environment.universe_id],
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
