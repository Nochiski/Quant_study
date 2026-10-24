/**
 * 정동민의 스토리 e2e — 빈 새 전략에서 말로 한 아이디어를 AI가 전략으로 바꿔 주고 바로 백테스트한다
 * (US-DM-03).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다. 공급자는 backend 대본
 * adapter이고, 질문의 "새 전략" 낱말이 `idea_to_new_strategy` 대본을 고른다(`_scenarios.py`). 그 대본은
 * 편집기의 `schema_version` 줄을 살려 전략 전체를 제안한다. 제안 미리보기·덮어쓰기 확인은 같은 스토리의
 * `assistant.workflow.spec.ts` 테스트가 본다.
 */
import { expect, test, type Locator, type Page } from "@playwright/test";

import {
  collapseAssistant,
  ask,
  assistant,
  ensureProvider,
  openAssistant,
} from "../assistant-helpers";
import {
  backtest,
  coveringElement,
  expectPhase,
  fillRunEnvironment,
  openEditor,
  runSettingsInputs,
  save,
  scrollPageTo,
  validate,
} from "../workbench-helpers";

/** 대본이 제안하는 전략 제목(`_scenarios.py`의 `_IDEA_TITLE`). */
const IDEA_TITLE = "KRX 대형 모멘텀";

/** 편집기 툴바의 네 조작. */
const toolbarControls = (page: Page) =>
  [
    ["실행 설정", runSettingsInputs(page).toggle],
    ["검증", validate(page)],
    ["리비전 저장", save(page)],
    ["백테스트", backtest(page)],
  ] as const;

/**
 * 편집기 칸의 조작이 옆 칸이나 겹쳐 뜬 패널에 깔리지 않고 제자리에서 눌린다. 1440px에서 편집기 칸이
 * 266px로 눌려 "검증"이 계약 칸 밑에 깔렸고(#269), 겹쳐 뜬 서랍이 편집기 오른쪽 칸을 덮었고(#290 리뷰
 * P2-1), 512px 이하 칸에서는 Graph·Diff 탭이 이력 버튼 밑에 깔렸다(#296). 조작마다 사용자가 그 조작을 보려고
 * 굴린 자리에서 찍는다. 중간 결과는 별도 작업공간의 명시 진입 버튼을 확인한다.
 */
const expectEditorReachable = async (
  page: Page,
  where: string,
  controls: readonly (readonly [string, Locator])[] = [
    ...toolbarControls(page),
    ["Graph 탭", page.getByRole("tab", { name: "그래프" })],
    ["YAML 탭", page.getByRole("tab", { name: "YAML" })],
    ["문서 상태", page.getByRole("status", { name: "문서 상태" })],
    ["전략 편집", page.getByRole("button", { name: "전략 편집", exact: true })],
    ["중간 결과", page.getByRole("button", { name: "중간 결과", exact: true })],
  ],
) => {
  for (const [name, control] of controls) {
    await scrollPageTo(control);
    expect(await coveringElement(control), `${where} ${name}`).toBeNull();
  }
};

/** 펼친 서랍의 머리 줄: 펼친 직후 포커스가 가는 자리와 머리 줄의 제목·닫기. */
type DrawerHead = {
  focus: Locator;
  controls: readonly (readonly [string, Locator])[];
};

const assistantHead = (page: Page): DrawerHead => ({
  focus: assistant(page),
  controls: [
    ["제목", assistant(page).getByRole("heading", { name: "AI 어시스턴트" })],
    [
      "AI 어시스턴트 접기",
      assistant(page).getByRole("button", { name: "AI 어시스턴트 접기" }),
    ],
  ],
});

const contractHead = (page: Page): DrawerHead => {
  const collapse = page.getByRole("button", { name: "계약 접기" });
  return {
    focus: collapse,
    controls: [
      [
        "제목",
        page
          .getByRole("complementary", { name: "계약" })
          .getByRole("heading", { name: "계약", exact: true }),
      ],
      ["계약 접기", collapse],
    ],
  };
};

/**
 * 서랍을 펼치고 굴리지 않은 채 머리 줄을 찍는다. 펼칠 때 포커스가 창보다 긴 패널로 가며 페이지를 굴리면
 * 머리 줄이 상단 바 밑에 깔렸다(#325, #290 리뷰 r3 P2-1). 굴림은 포커스와 함께 일어나므로 포커스가 옮겨 간
 * 것을 본 뒤에 잰다.
 */
const expectHeadAfterOpening = async (
  page: Page,
  where: string,
  open: () => Promise<void>,
  head: DrawerHead,
) => {
  await open();
  await expect(head.focus).toBeFocused();
  for (const [name, control] of head.controls) {
    expect(await coveringElement(control), `${where} ${name}`).toBeNull();
  }
};

test(
  "US-DM-03 빈 새 전략에서 AI에게 아이디어를 말해 받은 전략을 적용하고 백테스트한다",
  { tag: ["@story", "@US-DM-03"] },
  async ({ page }) => {
    test.setTimeout(240_000);
    await ensureProvider(page);
    await openEditor(page, "/research/strategies/new");
    // 보조 영역은 기본 접힘이다. 이 시나리오에서 비교할 구조·AI 조합을 직접 연다.
    await page.getByRole("button", { name: "전략 구조", exact: true }).click();
    // 빈 새 전략은 아직 전략이 아니다: 실행할 수 없다.
    await expect(backtest(page)).toBeDisabled();

    await openAssistant(page);
    await ask(page, "최근 많이 오른 대형주를 사는 새 전략을 만들어 줘");

    const card = assistant(page).getByRole("article", { name: IDEA_TITLE });
    await expect(card).toBeVisible({ timeout: 60_000 });
    await expect(card).toContainText("검증 통과");

    // "적용 후 백테스트": 문서는 적용되고 검증을 통과하지만, 실행 설정(기간·유니버스)은 전략 문서 밖이라
    // AI 적용이 채우지 않는다. 사람이 고친 문서와 같은 이유로 막히고 알림이 어느 칸인지 말한다(P3-02
    // 결정 1·5).
    await card.getByRole("button", { name: "적용 후 백테스트" }).click();
    await expectPhase(page, "검증 통과");
    await expect(
      page.getByText(/백테스트를 시작하지 않았습니다/u),
    ).toContainText("실행 설정에서 시작일·종료일·유니버스 칸을 채우세요.");
    await expect(backtest(page)).toBeDisabled();

    // AI 는 화면 위에 떠 있는 창이라 본문 폭을 나눠 갖지 않는다. 창을 최소화하면 편집기 조작이 어느 폭에서도
    // 옆 칸이나 떠 있는 요소 밑에 깔리지 않는다(#269·#290·#296 회귀 범위).
    await assistant(page).getByRole("button", { name: "AI 어시스턴트 접기" }).click();
    await expect(assistant(page)).toHaveCount(0);
    await expectEditorReachable(page, "1440");
    await page.setViewportSize({ width: 1280, height: 800 });
    await expectEditorReachable(page, "1280");
    await page.getByRole("button", { name: "중간 결과", exact: true }).click();
    await expect(page.getByRole("region", { name: "중간 결과", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "전략 편집", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "편집기", exact: true })).toBeVisible();
    await page.setViewportSize({ width: 1440, height: 900 });

    // 요약 띠의 "실행 설정 채우기"로 패널을 열어 기간·유니버스를 정한다.
    await fillRunEnvironment(page, { via: "band" });

    // 저장하지 않은 채로 돌려 본다. 적용한 문서는 시작 문서와 달라 저장되지 않은 상태(dirty)이므로,
    // 실행 화면으로 가기 전에 이탈 확인이 반드시 한 번 뜬다(`DirtyLeaveGuard`).
    await expect(backtest(page)).toBeEnabled();
    await backtest(page).click();
    const leaveGuard = page.getByRole("button", { name: "결과 보기" });
    await expect(leaveGuard).toBeVisible({ timeout: 60_000 });
    await expect(page).toHaveURL(/\/research\/strategies\/new/u);
    await leaveGuard.click();
    await expect(leaveGuard).toHaveCount(0);

    const runStatus = page.getByRole("status", { name: "실행 상태" });
    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    await expect(runStatus).toContainText("완료", { timeout: 120_000 });
    await expect(
      page.getByRole("article", { name: "백테스트 결과" }),
    ).toBeVisible({ timeout: 120_000 });
  },
);

test(
  "US-DM-03 AI 창과 계약 서랍을 어느 입구로 펼쳐도 머리 줄이 펼친 자리에서 눌린다",
  { tag: ["@story", "@US-DM-03"] },
  async ({ page }) => {
    await ensureProvider(page);
    await openEditor(page, "/research/strategies/new");
    // 처음 연 편집기는 AI 창을 최소화한 채 시작해 버튼을 가리지 않는다(PR #624 결정). 상단 바 토글로 열면 창은 화면
    // 위에 떠 있고 뷰포트 안에 있다.
    const floating = page.getByRole("complementary", { name: "AI 어시스턴트", exact: true });
    await expect(floating).toBeHidden();
    await expect(page.getByRole("button", { name: "AI 어시스턴트", exact: true })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    await page.getByRole("button", { name: "AI 어시스턴트", exact: true }).click();
    await expect(floating).toBeVisible();
    const box = await floating.boundingBox();
    const viewport = page.viewportSize()!;
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
    expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height);
    await expect(
      floating.getByRole("textbox", { name: "어시스턴트에게 보낼 메시지" }),
    ).toHaveAttribute("placeholder", /^생각을 말로 적어 주세요/u);
    // 최소화한 창을 여는 입구들을 본다 — 처음부터 열린 창을 먼저 접는다.
    await collapseAssistant(page);
    const launcherButton = page.getByRole("button", {
      name: "AI 어시스턴트",
      exact: true,
    });
    const contractToggle = page.getByRole("button", {
      name: "계약",
      exact: true,
    });
    // 경우마다 페이지 맨 위에서 연다. 계약 칸 내용이 본문을 늘려 페이지가 창보다 길다.
    const toTop = () => page.evaluate(() => window.scrollTo(0, 0));

    // 계약을 연 채 창을 연다. 여는 입구 셋(상단 바 토글·Alt+A·명령 팔레트) 모두 초점이 창으로 가고 제목 줄이 눌린다.
    await contractToggle.click();
    const entrances = [
      ["상단 바 토글", () => launcherButton.click()],
      ["Alt+A", () => page.keyboard.press("Alt+A")],
      [
        "명령 팔레트",
        async () => {
          await page.keyboard.press("Control+K");
          await page
            .getByRole("combobox", { name: "명령과 문서 경로 검색" })
            .fill("AI 어시스턴트");
          await page.keyboard.press("Enter");
        },
      ],
    ] as const;
    for (const [entrance, open] of entrances) {
      await toTop();
      await expectHeadAfterOpening(page, `1440 ${entrance}`, open, assistantHead(page));
      await page.keyboard.press("Alt+A");
    }

    // 1200(좁은 화면): 계약은 서랍으로 뜨고 머리 줄이 눌린다. AI 창과 자리를 다투지 않는다.
    await page.getByRole("button", { name: "계약 접기" }).click();
    await page.setViewportSize({ width: 1200, height: 800 });
    await toTop();
    await expectHeadAfterOpening(page, "1200 계약 서랍", () => contractToggle.click(), contractHead(page));
    await toTop();
    await expectHeadAfterOpening(page, "1200 AI 창", () => launcherButton.click(), assistantHead(page));
  },
);
