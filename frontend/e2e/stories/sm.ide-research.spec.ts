import { expect, test, type Page, type TestInfo } from "@playwright/test";
import {
  createStrategyDocument,
  getBacktestStatus,
  startBacktest,
  type ExperimentRequest,
} from "../../src/shared/api/generated";
import {
  apiClient,
  currentSource,
  fillRunEnvironment,
  GOLDEN,
  mustReplace,
  openEditorWorkspace,
  replaceSource,
  REQUESTED_ENVIRONMENT,
  requireData,
} from "../workbench-helpers";
import { waitForSettledDocument } from "../editor-helpers";
import { expectResearchMenuReadable } from "../ide-research/menu-checks";

const scenario = async (page: Page, info: TestInfo, width: number) => {
  let submitted = 0;
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/v1/experiments"
    )
      submitted++;
  });
  await page.setViewportSize({ width, height: 640 });
  const source = mustReplace(
    mustReplace(
      GOLDEN,
      "퀄리티 모멘텀",
      "연구",
    ),
    "parameters: []",
    "parameters:\n  - parameter_id: tilt\n    kind: float\n    default: 0.5\n    minimum: 0.0\n    maximum: 1.0\n    step: 0.5",
  );
  const saved = requireData(
    (
      await createStrategyDocument({
        client: apiClient,
        body: { format: "yaml", source },
      })
    ).data,
    "연구 기반 저장",
  );
  const run = requireData(
    (
      await startBacktest({
        client: apiClient,
        body: {
          strategy_source: {
            kind: "saved_revision",
            strategy_id: saved.strategy_id,
            revision: saved.revision,
            expected_spec_hash: saved.spec_hash,
          },
          environment: {
            ...REQUESTED_ENVIRONMENT,
            start: "2021-01-04",
            end: "2022-12-30",
          },
          parameter_values: { tilt: 0 },
        },
      })
    ).data,
    "선택 실행 접수",
  ).run;
  await expect
    .poll(
      async () =>
        requireData(
          (
            await getBacktestStatus({
              client: apiClient,
              path: { run_id: run.run_id },
            })
          ).data,
          "선택 실행 완료",
        ).status,
    )
    .toBe("completed");
  await page.goto(
    `/research/strategies/${saved.strategy_id}/revisions/${saved.revision}?run=${run.run_id}&view=yaml`,
  );
  await fillRunEnvironment(page);
  await expect(
    page.getByRole("button", { name: "백테스트", exact: true }),
  ).toBeEnabled();
  const menu = page.locator("summary").filter({ hasText: /^연구/u });
  await expect(menu).toContainText("N 1");
  const original = await currentSource(page);
  await menu.click();
  await expectResearchMenuReadable(page, menu);
  await page.screenshot({ path: info.outputPath(`short-title-menu-${width}.png`) });
  await expect(
    page.getByText(
      "선택한 실행의 파라미터 값을 사용합니다. 과거 실행 전체를 재현하지 않습니다.",
    ),
  ).toBeVisible();
  await menu.press("Tab");
  const ledgerLink = page.getByRole("link", { name: "전략 목록에서 시도 원장 찾기", exact: true });
  await expect(ledgerLink).toBeFocused();
  await ledgerLink.press("Escape");
  await expect(ledgerLink).not.toBeVisible();
  await expect(menu).toBeFocused();
  await menu.press("Space");
  await expect(ledgerLink).toBeVisible();
  await page.getByRole("button", { name: "레시피 탐색", exact: true }).click();
  await expect(page).toHaveURL(/recipe=true/u);
  await menu.click();
  await page.getByRole("button", { name: "레시피 탐색", exact: true }).click();
  await expect(page).not.toHaveURL(/recipe=true/u);
  await page.getByRole("tab", { name: "YAML", exact: true }).click();
  expect(await currentSource(page)).toBe(original);

  await replaceSource(page, `${original}\n# 미저장\n`);
  await menu.click();
  await expect(
    page.getByRole("button", { name: "실험으로 보내기", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText(/현재 원문을 리비전으로 저장한 뒤 보내세요/u),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath(`dirty-research-${width}.png`),
  });
  await menu.press("Tab");
  await page.keyboard.press("Escape");
  await openEditorWorkspace(page);
  await replaceSource(page, original);
  await expect(
    page.getByRole("button", { name: "백테스트", exact: true }),
  ).toBeEnabled();
  if (
    !(await page
      .getByRole("button", { name: "실험으로 보내기", exact: true })
      .isVisible())
  )
    await menu.click();
  const preview = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/v1/experiments/preview",
  );
  const send = page.getByRole("button", {
    name: "실험으로 보내기",
    exact: true,
  });
  await menu.focus();
  await menu.press("Tab");
  await page.keyboard.press("Tab");
  await expect(send).toBeFocused();
  await expect
    .poll(() =>
      send.evaluate((element) => {
        const box = element.getBoundingClientRect();
        const hit = document.elementFromPoint(
          box.x + box.width / 2,
          box.y + box.height / 2,
        );
        return hit !== null && element.contains(hit);
      }),
    )
    .toBe(true);
  await send.click();
  await expect(page).toHaveURL(/\/research\/experiments\/new$/u);
  const request = (await preview).postDataJSON() as ExperimentRequest;
  expect(request.run.parameter_values).toEqual({ tilt: 0 });
  expect(request.run.environment).toEqual(REQUESTED_ENVIRONMENT);
  expect(request.run.strategy_source).toEqual({
    kind: "saved_revision",
    strategy_id: saved.strategy_id,
    revision: saved.revision,
    expected_spec_hash: saved.spec_hash,
  });
  await expect(
    page.getByRole("button", { name: "대기열에 넣기", exact: true }),
  ).toBeVisible();
  const form = page.locator(".experiment-form");
  if (width < 768) {
    const bounds = await form.boundingBox();
    expect(bounds).not.toBeNull();
    for (const field of await form.locator("input, select, legend, .experiment-form__scroll-hint").all()) {
      // 탐색표의 값 입력은 이름 있는 내부 가로 스크롤에서 읽는다.
      if (await field.evaluate(element => element.closest(".data-list-page__scroll") !== null)) continue;
      const box = await field.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(bounds!.x);
      expect(box!.x + box!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width);
    }
  }
  const enqueue = page.getByRole("button", { name: "대기열에 넣기", exact: true });
  await enqueue.scrollIntoViewIfNeeded();
  await expect.poll(() => enqueue.evaluate(element => {
    const box = element.getBoundingClientRect();
    const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
    return hit !== null && element.contains(hit);
  })).toBe(true);
  expect(submitted).toBe(0);
  await form.screenshot({ path: info.outputPath(`experiment-full-form-${width}.png`) });
  await page.screenshot({
    path: info.outputPath(`experiment-seed-${width}.png`),
  });
};

test(
  "US-SM-12 편집기의 계열과 선택값을 확인하고 현재 환경으로 실험을 준비한다",
  { tag: ["@story", "@US-SM-12"] },
  async ({ page }, info) => {
    test.setTimeout(120_000);
    await menuGeometryScenario(page, info);
    await scenario(page, info, 1440);
  },
);
test(
  "US-SM-12 좁고 낮은 편집기에서 레시피와 미저장 안내를 읽고 실험을 준비한다",
  { tag: ["@story", "@US-SM-12"] },
  async ({ page }, info) => {
    test.setTimeout(120_000);
    await scenario(page, info, 360);
  },
);

// 기존 데스크톱 과업 안에서 제목 길이와 본문 폭 경계를 함께 확인한다.
const menuGeometryScenario = async (page: Page, info: TestInfo) => {
    for (const [label, title] of [
      ["short", "연구"],
      ["long", "현재 선택한 전략의 원문과 실행 환경을 유지하며 연구를 이어가는 긴 제목"],
    ]) {
      const saved = requireData(
        (await createStrategyDocument({ client: apiClient, body: {
          format: "yaml", source: mustReplace(GOLDEN, "퀄리티 모멘텀", title!),
        } })).data,
        "제목 길이별 메뉴 검증 전략",
      );
      await page.goto(`/research/strategies/${saved.strategy_id}/revisions/${saved.revision}?view=yaml`);
      await waitForSettledDocument(page);
      const menu = page.locator("summary").filter({ hasText: /^연구/u });
      for (const width of [768, 1024, 1440]) {
        await page.setViewportSize({ width, height: 640 });
        await menu.click();
        await expectResearchMenuReadable(page, menu);
        await page.screenshot({ path: info.outputPath(`${label}-title-menu-${width}.png`) });
        await menu.press("Escape");
        await expect(menu).toBeFocused();
      }
    }
};
