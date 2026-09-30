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
 * 굴린 자리에서 찍는다 — 1280×800에서 "중간 결과 접기"는 첫 화면 밖이다.
 */
const expectEditorReachable = async (
  page: Page,
  where: string,
  controls: readonly (readonly [string, Locator])[] = [
    ...toolbarControls(page),
    ["Graph 탭", page.getByRole("tab", { name: "Graph" })],
    ["Diff 탭", page.getByRole("tab", { name: "Diff" })],
    ["문서 상태", page.getByRole("status", { name: "문서 상태" })],
    ["중간 결과 접기", page.getByRole("button", { name: "중간 결과 접기" })],
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
      "사이드바 닫기",
      assistant(page).getByRole("button", { name: "사이드바 닫기" }),
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

    // 사이드바를 연 1440px: 편집기가 최소 폭을 지키도록 사이드바가 계약 자리에 겹쳐 뜬다.
    await expectEditorReachable(page, "1440+AI");
    // 전략 구조를 접으면 사이드바가 계약 옆에 붙는다(편집기 512px).
    await page.getByRole("button", { name: "전략 구조 접기" }).click();
    const assistantHandle = page.getByRole("separator", {
      name: "AI 어시스턴트 크기 조절",
    });
    await expect(assistantHandle).toBeVisible();
    await expectEditorReachable(page, "1440 전략 구조 접음+AI");
    // 사이드바를 가장 넓히면 편집기 칸이 툴바 한 줄보다 좁아진다(312px). 툴바 버튼이 다음 줄로 내려가
    // 옆 칸 밑에 깔리지 않는다(#290 리뷰 P3-2). 탭 목록은 이 폭에서 가로로 스크롤한다.
    await assistantHandle.focus();
    await page.keyboard.press("End");
    await expectEditorReachable(
      page,
      "1440 사이드바 최대 폭",
      toolbarControls(page),
    );
    await page.keyboard.press("Home");
    // 1280px(편집기 472px): 사이드바를 연 채로는 계약 자리에 겹쳐 뜨고, 닫으면 기본 배치다. 탭 줄이 다음
    // 줄로 내려간다.
    await page.getByRole("button", { name: "전략 구조", exact: true }).click();
    await page.setViewportSize({ width: 1280, height: 800 });
    await expectEditorReachable(page, "1280+AI");
    await assistant(page)
      .getByRole("button", { name: "사이드바 닫기" })
      .click();
    await expectEditorReachable(page, "1280");
    await page.setViewportSize({ width: 1440, height: 900 });

    // 요약 띠의 "실행 설정 채우기"로 패널을 열어 기간·유니버스를 정한다.
    await fillRunEnvironment(page, { via: "band" });

    // 저장하지 않은 채로 돌려 본다. 적용한 문서는 시작 문서와 달라 저장되지 않은 상태(dirty)이므로,
    // 실행 화면으로 가기 전에 이탈 확인이 반드시 한 번 뜬다(`DirtyLeaveGuard`).
    await expect(backtest(page)).toBeEnabled();
    await backtest(page).click();
    const leaveGuard = page.getByRole("button", { name: "나가기" });
    await expect(leaveGuard).toBeVisible({ timeout: 60_000 });
    await expect(page).toHaveURL(/\/research\/strategies\/new/u);
    await leaveGuard.click();
    await expect(leaveGuard).toHaveCount(0);

    const runStatus = page.getByRole("status", { name: "실행 상태" });
    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    await expect(runStatus).toContainText("completed", { timeout: 120_000 });
    await expect(
      page.getByRole("article", { name: "백테스트 결과" }),
    ).toBeVisible({ timeout: 120_000 });
  },
);

test(
  "US-DM-03 사이드바와 계약 서랍을 어느 입구로 펼쳐도 머리 줄이 펼친 자리에서 눌린다",
  { tag: ["@story", "@US-DM-03"] },
  async ({ page }) => {
    await ensureProvider(page);
    await openEditor(page, "/research/strategies/new");
    const assistantToggle = page.getByRole("button", {
      name: "AI 어시스턴트",
      exact: true,
    });
    const contractToggle = page.getByRole("button", {
      name: "계약",
      exact: true,
    });
    // 경우마다 페이지 맨 위에서 연다. 계약 칸 내용이 본문을 늘려 페이지가 창보다 길다.
    const toTop = () => page.evaluate(() => window.scrollTo(0, 0));

    // (가) 1440: 계약이 열린 채 사이드바를 연다 — 계약 자리에 겹쳐 뜬 서랍. 여는 입구 셋을 모두 본다.
    const entrances = [
      ["상단 바", () => assistantToggle.click()],
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
      await expectHeadAfterOpening(
        page,
        `1440 ${entrance}`,
        open,
        assistantHead(page),
      );
      await page.keyboard.press("Alt+A");
    }

    // (나) 1440: 계약을 접고 사이드바를 붙인 뒤 계약을 연다 — 붙은 사이드바 자리에 겹쳐 뜬 계약 서랍.
    await toTop();
    await page.getByRole("button", { name: "계약 접기" }).click();
    await expectHeadAfterOpening(
      page,
      "1440 붙은 사이드바",
      () => assistantToggle.click(),
      assistantHead(page),
    );
    await expectHeadAfterOpening(
      page,
      "1440 사이드바를 붙인 뒤 계약 서랍",
      () => contractToggle.click(),
      contractHead(page),
    );
    await page.getByRole("button", { name: "계약 접기" }).click();
    await page.keyboard.press("Alt+A");
    await contractToggle.click();

    // 1280: 계약이 붙은 기본 배치에서 사이드바를 연다 — 계약 자리에 겹쳐 뜬 서랍.
    await page.setViewportSize({ width: 1280, height: 800 });
    await toTop();
    await expectHeadAfterOpening(
      page,
      "1280 사이드바 서랍",
      () => assistantToggle.click(),
      assistantHead(page),
    );
    await page.keyboard.press("Alt+A");

    // 1200(좁은 화면): 오른쪽 패널이 모두 접히고, 사이드바와 계약은 각각 서랍으로 뜬다.
    await page.setViewportSize({ width: 1200, height: 800 });
    await toTop();
    await expectHeadAfterOpening(
      page,
      "1200 사이드바 서랍",
      () => assistantToggle.click(),
      assistantHead(page),
    );
    await page.keyboard.press("Alt+A");
    await toTop();
    await expectHeadAfterOpening(
      page,
      "1200 계약 서랍",
      () => contractToggle.click(),
      contractHead(page),
    );
  },
);
