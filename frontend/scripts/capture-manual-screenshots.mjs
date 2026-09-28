import { chromium, expect } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, readFile } from "node:fs/promises";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { backendPort, previewOrigin } from "../e2e/ports.mjs";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDirectory, "../..");
const backendDirectory = resolve(repositoryRoot, "backend");
const outputDirectory = resolve(
  repositoryRoot,
  "docs/manual/strategy-workbench/assets",
);
const fixtureDirectory = resolve(
  backendDirectory,
  "tests/fixtures/strategy_documents",
);
// 01~05·16 편집기 글은 매뉴얼 1절 샘플 그대로다. `test_manual_sample_compiles` 와 같은 규칙
// ("## 1." 뒤 첫 yaml 블록)으로 읽어, 사용자가 붙여 넣는 글과 그림이 같게 한다(#263 리뷰 P3-2).
const manualPath = resolve(
  repositoryRoot,
  "docs/manual/strategy-workbench/README.md",
);
// 승격 노드(18)를 보일 조건형 아이디어. 비교 출력(참/거짓)을 compile 이 0/1 점수로 승격한다.
const promotedIdeaPath = resolve(fixtureDirectory, "ideas/ma20_breakout.yaml");
// 포트 기본값은 e2e 와 같은 상수에서 온다 — 여기만 숫자를 따로 적으면 워크트리가 포트를 옮겼을 때
// 이 스크립트만 옛 포트를 부른다(1차 리뷰 P3-8).
const frontendUrl =
  process.env.WORKBENCH_MANUAL_FRONTEND_URL ?? previewOrigin();
const backendUrl =
  process.env.WORKBENCH_MANUAL_BACKEND_URL ??
  `http://127.0.0.1:${backendPort()}`;
const headed = process.env.WORKBENCH_MANUAL_HEADED === "1";
// backend 가 쓰는 SQLite 파일(`STRATEGY_WORKBENCH_DB_PATH`와 같은 값). 업그레이드 배너(17)는 은퇴
// 버전(1.1) revision 이 있어야 뜨는데, 그 row 는 공개 API 로 만들 수 없어 backend 테스트 헬퍼 CLI 로
// 이 파일에 직접 심는다(`frontend-testing.md`의 은퇴 row seeding 규칙).
const databasePath = process.env.WORKBENCH_MANUAL_DB_PATH;

/** e2e `workbench-helpers.ts`의 `RUN_ENVIRONMENT`와 같은 값. 이 스크립트는 node 로 바로 돌아 TS 헬퍼를 import 하지 못한다. */
const RUN_ENVIRONMENT = {
  start: "2021-01-01",
  end: "2026-08-31",
  universe_id: "krx.common-stock",
};
/** 결과 설명(19)의 AI 공급자. backend 대본 공급자(`STRATEGY_WORKBENCH_ASSISTANT_FAKE_PROVIDER=1`)는 키를 검사하지 않는다. */
const PROVIDER_LABEL = "Claude";
const PROVIDER_KEY = "sk-manual-capture-key-0000";
const VIEWPORT = { width: 1600, height: 1000 };

const assertReachable = async (url, label) => {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${label} 응답 실패: ${response.status} ${url}`);
  }
};

/**
 * 원문이 저장된 schema 1.1 revision row 하나를 backend DB 에 심고 전략 ID 를 돌려준다. owner 는
 * `backend/tests/frozen_revision_rows.py` CLI 다 — e2e 의 `seedFrozenRevisionRows`와 같은 호출에
 * `STRATEGY_WORKBENCH_E2E_SEED_SCHEMA=1.1`만 더한다. `--no-sync`: `uv run`의 환경 동기화가
 * `maturin develop`으로 넣은 Rust core 를 걷어 내면 이어지는 백테스트가 `CoreUnavailable`로 죽는다.
 */
const seedRetiredRevision = () => {
  const suffix = `-${Date.now().toString(36)}`;
  const result = spawnSync(
    "uv",
    ["run", "--no-sync", "python", "tests/frozen_revision_rows.py"],
    {
      cwd: backendDirectory,
      encoding: "utf8",
      shell: process.platform === "win32",
      env: {
        ...process.env,
        STRATEGY_WORKBENCH_E2E_DB: databasePath,
        STRATEGY_WORKBENCH_E2E_SEED_SUFFIX: suffix,
        STRATEGY_WORKBENCH_E2E_SEED_SCHEMA: "1.1",
      },
    },
  );
  if (result.status !== 0) {
    throw new Error(
      `1.1 revision seeding 실패 — status=${result.status} db=${databasePath} stdout=${result.stdout} stderr=${result.stderr}`,
    );
  }
  return `retired-1-1${suffix}`;
};

const capture = async (page, filename, locator) => {
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  const viewport = page.viewportSize() ?? VIEWPORT;
  await page.mouse.move(viewport.width - 10, viewport.height - 10);
  await page.waitForTimeout(100);
  const target = resolve(outputDirectory, filename);
  const options = {
    path: target,
    animations: "disabled",
    caret: "hide",
  };
  if (locator === undefined) {
    await page.evaluate(() => window.scrollTo({ top: 0, left: 0 }));
    await page.screenshot(options);
  } else {
    await locator.screenshot(options);
  }
  process.stdout.write(`스크린샷 저장: ${target}\n`);
};

/**
 * 영역 아래 끝이 뷰포트 밖이면 뷰포트 높이를 그 끝까지 늘린다. 앱 레이아웃이 뷰포트 높이를 따라
 * 다시 배치되므로(`vh` 단위 등) 끝이 들어올 때까지 몇 번 되풀이한다. `settle`은 매번 크기를 바꾼 뒤
 * 다시 맞출 스크롤 같은 준비 단계다.
 */
const fitViewportTo = async (page, locator, settle = async () => {}) => {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    await settle();
    const box = await locator.boundingBox();
    if (box === null) throw new Error("뷰포트를 맞출 영역이 화면에 없습니다.");
    const current = page.viewportSize() ?? VIEWPORT;
    const needed = Math.ceil(box.y + box.height + 24);
    if (needed <= current.height) return;
    await page.setViewportSize({ width: VIEWPORT.width, height: needed });
  }
  throw new Error("영역이 뷰포트 안에 들어오지 않습니다.");
};

/**
 * 영역을 안쪽 스크롤 컨테이너 안에서 아래 끝이 보이게 맞추고 창 스크롤은 맨 위로 되돌린다. 창이
 * 스크롤된 채로 찍으면 고정 상단바가 영역 위를 덮는다(14). 뷰포트를 늘리는 `fitViewportTo`와 함께
 * 쓰면 영역 전체가 상단바 아래에 들어온다.
 */
const alignBelowTopBar = async (page, locator) => {
  await locator.evaluate((element) => element.scrollIntoView({ block: "end" }));
  await page.evaluate(() => window.scrollTo({ top: 0, left: 0 }));
};

/**
 * 영역이 뷰포트보다 길면 고정 상단바가 영역 위에 겹쳐 찍힌다(14). 그 장면만 영역이 다 들어가는
 * 높이로 뷰포트를 늘려 찍고 원래대로 돌린다.
 */
const captureTall = async (page, filename, locator) => {
  try {
    await fitViewportTo(page, locator, () => alignBelowTopBar(page, locator));
    await capture(page, filename, locator);
  } finally {
    await page.setViewportSize(VIEWPORT);
  }
};

/** `top` 영역의 위 끝부터 `bottom` 영역의 아래 끝까지를 한 장으로 찍는다(18). */
const captureSpan = async (page, filename, top, bottom) => {
  try {
    await fitViewportTo(page, bottom, () => alignBelowTopBar(page, bottom));
    await page.evaluate(() => document.fonts.ready.then(() => undefined));
    const viewport = page.viewportSize() ?? VIEWPORT;
    await page.mouse.move(viewport.width - 10, viewport.height - 10);
    await page.waitForTimeout(100);
    const topBox = await top.boundingBox();
    const bottomBox = await bottom.boundingBox();
    if (topBox === null || bottomBox === null) {
      throw new Error(`${filename}: 찍을 영역이 화면에 없습니다.`);
    }
    const target = resolve(outputDirectory, filename);
    await page.screenshot({
      path: target,
      animations: "disabled",
      caret: "hide",
      clip: {
        x: topBox.x,
        y: topBox.y,
        width: topBox.width,
        height: bottomBox.y + bottomBox.height - topBox.y,
      },
    });
    process.stdout.write(`스크린샷 저장: ${target}\n`);
  } finally {
    await page.setViewportSize(VIEWPORT);
  }
};

/** 편집기 검증(parse·compile)이 입력을 따라잡을 때까지 기다린다 — e2e `waitForSettledDocument`와 같다(#240). */
const expectPhase = async (page, phase) => {
  const status = page.getByRole("status", { name: "문서 상태" });
  await expect(status).toHaveAttribute("data-settled", "true");
  await expect(status).toContainText(phase);
};

/** 편집기 자동완성 팝업을 닫는다. `fill` 뒤 커서 자리의 제안이 떠 있으면 편집기 본문을 가린다(02·04). */
const closeCompletion = async (page) => {
  const popup = page.locator(".cm-tooltip-autocomplete");
  if ((await popup.count()) > 0) {
    await page
      .getByRole("textbox", { name: "편집기", exact: true })
      .press("Escape");
  }
  await expect(popup).toHaveCount(0);
};

if (databasePath === undefined || !isAbsolute(databasePath)) {
  throw new Error(
    "WORKBENCH_MANUAL_DB_PATH 에 backend 의 STRATEGY_WORKBENCH_DB_PATH 와 같은 절대 경로를 넣으세요(업그레이드 배너 장면용).",
  );
}
await assertReachable(`${backendUrl}/api/v1/health`, "백엔드");
await assertReachable(frontendUrl, "프론트엔드");
if (!existsSync(databasePath)) {
  throw new Error(`backend DB 파일이 없습니다: ${databasePath}`);
}
await mkdir(outputDirectory, { recursive: true });

const manualText = (await readFile(manualPath, "utf8")).replace(/\r\n?/gu, "\n");
const manualSample = /```yaml\n([\s\S]*?)```/u.exec(
  manualText.slice(manualText.indexOf("## 1.")),
)?.[1];
if (manualSample === undefined) {
  throw new Error("매뉴얼 1절에 yaml 블록이 없습니다.");
}
const promotedIdea = (await readFile(promotedIdeaPath, "utf8")).replace(
  /\r\n?/gu,
  "\n",
);
const titleV1 = "사용자 매뉴얼 모멘텀";
const titleV2 = `${titleV1} 개선안`;
const sourceV1 = manualSample;
if (!sourceV1.includes(`title: "${titleV1}"`)) {
  throw new Error(`매뉴얼 1절 샘플의 title 이 "${titleV1}" 이 아닙니다.`);
}
const sourceV2 = sourceV1.replace(titleV1, titleV2);

const browser = await chromium.launch({
  headless: !headed,
  // Windows Chromium 의 날짜 칸 자리표시자는 브라우저 UI 언어를 따른다 — e2e 설정과 같이 고정한다.
  args: ["--lang=ko-KR"],
});
const context = await browser.newContext({
  locale: "ko-KR",
  timezoneId: "Asia/Seoul",
  viewport: VIEWPORT,
  colorScheme: "light",
});
const page = await context.newPage();
// 저장하지 않은 초안(18)을 떠날 때 뜨는 beforeunload 확인은 받아들인다.
page.on("dialog", (dialog) => void dialog.accept());
const editor = page.getByRole("textbox", { name: "편집기", exact: true });
const documentStatus = page.getByRole("status", { name: "문서 상태" });
const saveButton = page.getByRole("button", {
  name: "리비전 저장",
  exact: true,
});
const backtestButton = page.getByRole("button", {
  name: "백테스트",
  exact: true,
});
const settingsToggle = page.getByLabel("실행 설정 열기");
const summaryBand = page.getByRole("region", { name: "실행 설정 요약" });
// 실행 설정 팝오버는 이름이 없다. 첫 묶음(fieldset "실행 환경")의 부모가 팝오버 전체다.
const settingsPopover = page
  .getByRole("group", { name: "실행 환경", exact: true })
  .locator("xpath=..");
const debuggerRegion = page.getByRole("region", { name: "중간 결과" });
const backtestPosts = [];
page.on("request", (request) => {
  if (
    request.method() === "POST" &&
    new URL(request.url()).pathname === "/api/v1/backtests"
  ) {
    backtestPosts.push(request);
  }
});

try {
  await page.goto(`${frontendUrl}/research/strategies/new`);
  await expect(editor).toBeVisible();
  // 빈 템플릿의 검증이 끝나기 전에 찍으면 실행마다 배지·문제 목록이 달라진다(구문 통과 / 검증 오류).
  await expectPhase(page, "검증 오류");
  await capture(page, "01-new-strategy.png");

  await editor.fill(sourceV1);
  await expectPhase(page, "검증 통과");
  await closeCompletion(page);
  await capture(page, "02-valid-yaml.png");

  const outline = page.getByRole("tree", { name: "StrategySpec 문서 구조" });
  const risk = outline.getByRole("treeitem", { name: "risk", exact: true });
  await risk.focus();
  if ((await risk.getAttribute("aria-expanded")) !== "true") {
    await risk.press("ArrowRight");
  }
  await outline
    .getByRole("treeitem", { name: "max_name_weight", exact: true })
    .click();
  await expect(page.getByText("5%", { exact: true })).toBeVisible();
  await capture(page, "03-contract-inspector.png");

  const invalidSource = sourceV1.replace("max_name_weight", "max_name_wieght");
  await editor.fill(invalidSource);
  await expectPhase(page, "구조 오류");
  await expect(page.getByRole("region", { name: "문제" })).toContainText(
    "/risk/max_name_wieght",
  );
  await closeCompletion(page);
  await capture(page, "04-structure-error.png");

  await editor.fill(sourceV1);
  await expectPhase(page, "검증 통과");
  await expect(saveButton).toBeEnabled();
  const createdResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/strategy-documents" &&
      response.status() === 201,
  );
  await saveButton.click();
  const createdDocument = await (await createdResponse).json();
  await expect(page).toHaveURL(
    /\/research\/strategies\/[^/]+\/revisions\/1(?:\?.*)?$/u,
  );
  await expect(
    page.locator(
      `.doc-toolbar__identity code[title="${createdDocument.spec_hash}"]`,
    ),
  ).toBeVisible();
  await expect(documentStatus).toContainText("저장됨");
  // 저장 직후 revision 화면이 원문을 다시 분석하는 동안(`편집 중`, 구조 트리 "분석 중")을 찍지 않는다.
  await expectPhase(page, "저장됨");
  await expect(page.getByText("문서 구조를 분석하는 중입니다.")).toHaveCount(0);
  const revisionOneUrl = page.url();
  const strategyId = new URL(revisionOneUrl).pathname.split("/")[3];
  if (strategyId === undefined || strategyId === "") {
    throw new Error(`저장된 전략 ID를 찾을 수 없습니다: ${revisionOneUrl}`);
  }
  await capture(page, "05-saved-revision.png");

  await editor.fill(sourceV2);
  await expectPhase(page, "검증 통과");
  const revisedResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname ===
        `/api/v1/strategy-documents/${strategyId}/revisions` &&
      response.status() === 201,
  );
  await saveButton.click();
  const revisedDocument = await (await revisedResponse).json();
  expect(revisedDocument.spec_hash).not.toBe(createdDocument.spec_hash);
  await expect(page).toHaveURL(
    new RegExp(
      `/research/strategies/${strategyId}/revisions/2(?:\\?.*)?$`,
      "u",
    ),
  );
  await expect(
    page.locator(
      `.doc-toolbar__identity code[title="${revisedDocument.spec_hash}"]`,
    ),
  ).toBeVisible();
  await expect(documentStatus).toContainText("저장됨");
  await page.getByRole("tab", { name: "Diff", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "StrategySpec Diff" }),
  ).toContainText("/title");
  await capture(page, "06-revision-diff.png");

  // 5절: 실행 설정(기간·유니버스)을 채우기 전에는 추적이 막히고 중간 결과가 이유를 말한다.
  await page.getByRole("tab", { name: "YAML", exact: true }).click();
  await expect(summaryBand).toContainText(
    "실행 설정에서 시작일·종료일·유니버스 칸을 채우세요.",
  );
  // 차단 문장은 입력 칸 아래에 있어 기본 높이에서는 잘린다. 영역을 최대로 키워 문장까지 담는다.
  const debuggerHandle = page.getByRole("separator", {
    name: "중간 결과 크기 조절",
  });
  await debuggerHandle.focus();
  await debuggerHandle.press("End");
  await expect(debuggerHandle).toHaveAttribute("aria-valuenow", "480");
  const traceBlocked = debuggerRegion
    .getByRole("status")
    .filter({ hasText: "추적은 실행 설정 위에서 돕니다." });
  // 편집기 글 길이에 따라 중간 결과가 화면 아래로 밀릴 수 있어 문장까지 스크롤한다.
  await traceBlocked.scrollIntoViewIfNeeded();
  await expect(traceBlocked).toBeInViewport();
  await capture(page, "20-trace-blocked.png", debuggerRegion);

  // 6절: 실행 설정 패널. e2e `fillRunEnvironment`(toggle 경로)와 같은 단계·셀렉터다.
  await expectPhase(page, "저장됨");
  await settingsToggle.click();
  const environmentFields = [
    [page.getByLabel("시작일", { exact: true }), RUN_ENVIRONMENT.start],
    [page.getByLabel("종료일", { exact: true }), RUN_ENVIRONMENT.end],
    [
      page.getByRole("textbox", { name: "유니버스", exact: true }),
      RUN_ENVIRONMENT.universe_id,
    ],
  ];
  for (const [field, value] of environmentFields) {
    await field.fill(value);
    await expect(field).toHaveValue(value);
  }
  await expect(summaryBand).toContainText(RUN_ENVIRONMENT.universe_id);
  // 팝오버는 뷰포트 70%(최대 720px)에서 안쪽 스크롤로 잘린다. 매뉴얼에는 두 묶음(실행 환경·실행
  // 옵션)이 한 장에 다 보이도록 이 장면에서만 높이 제한을 풀고 찍은 뒤 되돌린다.
  await page.getByRole("textbox", { name: "유니버스", exact: true }).blur();
  await settingsPopover.evaluate((element) => {
    element.style.maxHeight = "none";
  });
  try {
    await captureTall(page, "15-run-settings-panel.png", settingsPopover);
  } finally {
    await settingsPopover.evaluate((element) => {
      element.style.maxHeight = "";
    });
  }
  await settingsToggle.click();
  await expect(settingsPopover).toBeHidden();
  await expect(summaryBand).toContainText(RUN_ENVIRONMENT.start);
  await expect(summaryBand).toContainText(RUN_ENVIRONMENT.end);
  await capture(page, "16-summary-band.png");

  await page
    .getByRole("textbox", { name: "종목 ID", exact: true })
    .fill("sec-005930-1, sec-000660-1, sec-035420-1");
  await page
    .getByRole("combobox", { name: "노드", exact: true })
    .selectOption("mom_252");
  await page.getByRole("button", { name: "추적 실행" }).click();
  await expect(page.getByLabel("추적 재현 정보")).toBeVisible({
    timeout: 60_000,
  });
  await page.getByRole("tab", { name: "TargetTape" }).click();
  const targetTape = page.getByRole("region", {
    name: "TargetTape 후보와 선택 노드 결과",
  });
  await expect(targetTape).toBeVisible();
  await expect(debuggerHandle).toHaveAttribute("aria-valuenow", "480");
  // 중간 결과 영역은 스크롤이 두 겹이다(영역 본문 안에 탭 패널). 바깥은 끝까지 내려 탭 패널에 자리를
  // 주고, 안쪽은 맨 위로 올려 표 머리 행부터 세 종목 행이 다 보이게 한다. `scrollIntoView`는 안쪽까지
  // 끝으로 내려 머리 행을 가린다.
  await targetTape.evaluate((element) => {
    const scrollables = [];
    for (
      let node = element;
      node !== null && node.getAttribute("aria-label") !== "중간 결과";
      node = node.parentElement
    ) {
      if (
        node.scrollHeight > node.clientHeight &&
        getComputedStyle(node).overflowY !== "visible"
      ) {
        scrollables.push(node);
      }
    }
    const outer = scrollables.pop();
    for (const inner of scrollables) inner.scrollTop = 0;
    if (outer !== undefined) outer.scrollTop = outer.scrollHeight;
  });
  await capture(page, "07-debug-trace.png", debuggerRegion);

  expect(backtestPosts).toHaveLength(0);
  await settingsToggle.click();
  await page
    .getByRole("combobox", { name: "실행 core" })
    .selectOption("python");
  // 6절 표의 값을 먼저 채우고 초기 자본만 0으로 둔다 — 실패 화면(08)도 표와 같은 벤치마크를 보여야 한다(#148 리뷰).
  await page
    .getByRole("textbox", { name: "벤치마크 종목 ID" })
    .fill("sec-005930-1");
  await page.getByRole("spinbutton", { name: "연환산 거래일" }).fill("252");
  const initialCash = page.getByRole("spinbutton", { name: "초기 자본 (KRW)" });
  await initialCash.fill("0");
  const rejectedRun = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/backtests",
  );
  await backtestButton.click();
  const rejectedResponse = await rejectedRun;
  expect(rejectedResponse.status()).toBe(422);
  expect(rejectedResponse.request().postDataJSON()).toMatchObject({
    core: "python",
    initial_cash: 0,
    environment: RUN_ENVIRONMENT,
  });
  const rejectedPayload = await rejectedResponse.json();
  expect(Array.isArray(rejectedPayload.detail)).toBe(true);
  expect(rejectedPayload.detail).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        loc: ["body"],
        msg: expect.stringContaining("initial_cash must be positive"),
        type: "value_error",
        input: expect.objectContaining({ initial_cash: 0 }),
      }),
    ]),
  );
  expect(backtestPosts).toHaveLength(1);
  expect(new URL(page.url()).pathname).toBe(
    `/research/strategies/${strategyId}/revisions/2`,
  );
  await expect(page.getByRole("alert")).toContainText("백테스트 시작 실패");
  // 팝오버는 안쪽 스크롤이라 맨 아래 실행 옵션(초기 자본 0)이 잘린다. 팝오버를 끝까지 내려 실패
  // 원인 칸과 상단 오류 문장이 한 화면에 들어오게 하고, 팝오버 아래 끝이 뷰포트 밖이면 이 장면만
  // 뷰포트를 늘린다.
  try {
    await fitViewportTo(page, settingsPopover, () =>
      settingsPopover.evaluate((element) => {
        element.scrollTop = element.scrollHeight;
      }),
    );
    await expect(initialCash).toBeInViewport();
    await capture(page, "08-backtest-error.png");
  } finally {
    await page.setViewportSize(VIEWPORT);
  }

  await initialCash.fill("100000000");
  await expect(page.getByText("준비됨", { exact: true })).toBeVisible();
  await settingsToggle.click();
  const acceptedRun = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/backtests",
  );
  await backtestButton.click();
  const acceptedResponse = await acceptedRun;
  expect(acceptedResponse.status()).toBe(202);
  expect(acceptedResponse.request().postDataJSON()).toMatchObject({
    core: "python",
    initial_cash: 100_000_000,
    benchmark_security_id: "sec-005930-1",
    annualization_days: 252,
    environment: RUN_ENVIRONMENT,
    strategy_source: {
      kind: "saved_revision",
      strategy_id: strategyId,
      revision: 2,
      expected_spec_hash: revisedDocument.spec_hash,
    },
  });
  const acceptedPayload = await acceptedResponse.json();
  const runId = acceptedPayload.run?.run_id;
  if (typeof runId !== "string" || runId === "") {
    throw new Error("백테스트 실행 ID가 202 응답에 없습니다.");
  }
  expect(backtestPosts).toHaveLength(2);
  const resultUrl = `${frontendUrl}/research/backtests/${runId}`;
  await expect(page).toHaveURL(resultUrl);
  await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
    "completed",
    { timeout: 120_000 },
  );
  await expect(
    page.getByRole("heading", { name: "백테스트 결과" }),
  ).toBeVisible();
  await expect(page.getByRole("article", { name: "백테스트 결과" })).toHaveCSS(
    "display",
    "grid",
  );
  await expect(page.getByRole("region", { name: "핵심 성과 지표" })).toHaveCSS(
    "grid-template-columns",
    /^(?!none$).+/u,
  );
  await capture(page, "09-backtest-result.png");

  await page.getByRole("link", { name: "백테스트", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "백테스트 이력" }),
  ).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: runId })).toHaveCount(1);
  await capture(page, "10-backtest-history.png");

  await page.getByRole("link", { name: "전략", exact: true }).click();
  await expect(page.getByRole("heading", { name: "전략 이력" })).toBeVisible();
  await page
    .getByRole("button", {
      name: `Revision 펼치기: ${titleV2} (${strategyId})`,
    })
    .click();
  await expect(
    page.getByRole("region", {
      name: `저장 revision 목록: ${titleV2} (${strategyId})`,
    }),
  ).toContainText("v1");
  await expect(
    page.getByRole("region", {
      name: `저장 revision 목록: ${titleV2} (${strategyId})`,
    }),
  ).toContainText("v2");
  await capture(page, "11-strategy-history.png");

  await page.goto(
    `${frontendUrl}/research/strategies/${strategyId}/revisions/2`,
  );
  await expect(editor).toBeVisible();
  // 검증이 끝난 뒤 찍는다 — 분석 중이면 배지가 `구문 통과`로 찍힌다(#263 리뷰 P3-1).
  await expectPhase(page, "저장됨");
  await page.keyboard.press("Control+K");
  await expect(page.getByRole("dialog")).toBeVisible();
  await capture(page, "12-command-palette.png");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();

  // 8절: Form 탭(섹션별 필드)과 Graph 탭(노드 목록 + 선택한 노드 속성).
  await page.getByRole("tab", { name: "Form", exact: true }).click();
  await expect(page.getByRole("tabpanel", { name: "Form" })).toContainText(
    "max_name_weight",
  );
  await capture(page, "13-form-editing.png");

  await page.getByRole("tab", { name: "Graph", exact: true }).click();
  const graphEditor = page.getByRole("region", { name: "그래프 편집" });
  await expect(graphEditor).toBeVisible();
  await graphEditor
    .getByRole("button", { name: "노드 편집: mom_252", exact: true })
    .click();
  await expect(
    graphEditor.getByRole("group", { name: /선택한 노드/ }),
  ).toContainText("input_node_id");
  // 편집 표면(노드 목록 + 선택한 노드 속성)만 담는다 — 전체 화면은 DAG 카드가 차지해 편집기가 잘린다.
  // 영역이 뷰포트보다 길어 고정 상단바가 겹치므로 뷰포트를 늘려 찍는다.
  await captureTall(page, "14-graph-editing.png", graphEditor);

  // 7절: 저장된 schema 1.1 revision 을 열면 업그레이드 배너가 뜬다(새 전략 화면에는 없다, #257).
  // 전략 이력(11)에 섞이지 않도록 그 장면을 찍은 뒤에 심는다.
  const retiredStrategyId = seedRetiredRevision();
  await page.goto(
    `${frontendUrl}/research/strategies/${retiredStrategyId}/revisions/1`,
  );
  await expect(editor).toBeVisible();
  const upgradeBanner = page.getByRole("region", { name: "이전 schema 문서" });
  await expect(upgradeBanner).toContainText(
    "이 문서는 지원이 끝난 schema 버전입니다",
  );
  await expect(
    upgradeBanner.getByRole("button", { name: "현재 버전으로 업그레이드" }),
  ).toBeEnabled();
  await expectPhase(page, "구조 오류");
  await closeCompletion(page);
  await capture(page, "17-upgrade-banner.png");

  // 8절 Graph: 조건형 아이디어의 비교 출력(참/거짓)에 compile 이 붙인 승격 노드.
  await page.goto(`${frontendUrl}/research/strategies/new`);
  await expect(editor).toBeVisible();
  await editor.fill(promotedIdea);
  await expectPhase(page, "검증 통과");
  await closeCompletion(page);
  await page.getByRole("tab", { name: "Graph", exact: true }).click();
  const promotedNode = page.getByRole("button", {
    name: "그래프 노드 선택: 참/거짓을 1/0으로",
    exact: true,
  });
  await expect(promotedNode).toBeVisible();
  // DAG 카드(제목·계획 요약·노드 목록)만 담는다. 같은 영역 아래의 그래프 편집 표면은 14가 보인다.
  const dagSection = page.getByRole("region", {
    name: "FactorGraph DAG",
    exact: true,
  });
  const dagCanvas = page
    .getByRole("list", { name: "백엔드 계획 순서의 팩터 노드와 입력 연결" })
    .locator("xpath=..");
  // 노드 목록은 가로 스크롤이다. 승격 노드는 계획의 마지막이라 오른쪽 끝으로 민다.
  await dagCanvas.evaluate((element) => {
    element.scrollLeft = element.scrollWidth;
  });
  await expect(promotedNode).toBeInViewport();
  await captureSpan(page, "18-promoted-node.png", dagSection, dagCanvas);

  // 9절: AI 공급자를 등록하고(e2e `ensureProvider`와 같은 단계) 결과 화면에서 AI 에게 묻는다.
  await page.goto(`${frontendUrl}/settings`);
  const providers = page.getByRole("region", { name: "AI 어시스턴트 공급자" });
  await expect(providers.getByLabel("표시 이름")).toBeVisible();
  if ((await providers.getByTestId("provider-active").count()) === 0) {
    await providers.getByLabel("표시 이름").fill(PROVIDER_LABEL);
    await providers.getByLabel("API 키").fill(PROVIDER_KEY);
    await providers
      .getByRole("button", { name: "연결 테스트 후 저장" })
      .click();
    await expect(
      providers.getByRole("heading", { name: PROVIDER_LABEL }),
    ).toBeVisible();
  }
  await page.goto(resultUrl);
  await expect(page.getByRole("article", { name: "백테스트 결과" })).toBeVisible(
    { timeout: 60_000 },
  );
  await page.getByRole("button", { name: "AI에게 결과 묻기" }).click();
  const assistant = page.getByRole("complementary", { name: "AI 어시스턴트" });
  await expect(
    assistant.getByRole("heading", { name: "AI 어시스턴트" }),
  ).toBeVisible();
  await assistant
    .getByRole("textbox", { name: "어시스턴트에게 보낼 메시지" })
    .fill("이 결과 좋은 거야?");
  await assistant.getByRole("button", { name: "보내기" }).click();
  await expect(
    assistant.getByRole("status", { name: "진행 상태" }),
  ).toHaveText("답변이 완료되었습니다.", { timeout: 60_000 });
  await expect(
    assistant.getByRole("log", { name: "대화 내용" }),
  ).toContainText("같은 기간 벤치마크(sec-005930-1)는");
  await capture(page, "19-ai-result-explain.png");

  process.stdout.write(`매뉴얼 전략 ID: ${strategyId}\n`);
} finally {
  await context.close();
  await browser.close();
}
