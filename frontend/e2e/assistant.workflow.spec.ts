/**
 * AI 어시스턴트를 사용자 관점에서 끝까지 돌리는 브라우저 시나리오 (WORKFLOW B-05).
 *
 * 설정에서 공급자를 등록하고 → 전략 화면 우측 사이드바에서 묻고 → 스트리밍 답변·검색 출처·제안
 * 카드를 보고 → 제안을 문서에 적용해 검증을 통과시키고 → 백테스트까지 간다.
 *
 * 공급자는 backend의 **대본 adapter**다(`adapters/outbound/llm_scripted`,
 * `STRATEGY_WORKBENCH_ASSISTANT_FAKE_PROVIDER=1`을 `playwright.config.ts`가 켠다). 공급자 응답만
 * 브라우저에서 가로채지 않는 이유는, 그러면 SSE 프레이밍·sequence·턴 러너·제안 재검증이 전부
 * 검사 밖으로 나가기 때문이다. 여기서 가짜인 것은 모델 하나뿐이고 나머지 경로는 전부 진짜다.
 *
 * 대본은 질문에 든 낱말로 고른다(`_scenarios.py`의 `SCENARIO_KEYWORDS`): "제안"이면 도구 호출 후
 * 제안, "검색"이면 검색 활동 후 검증 3회 실패, "천천히"면 긴 스트리밍, 그 밖이면 짧은 답변이다.
 *
 * 스크린샷은 만들지 않는다. 시각 기준선의 owner는 `workbench.infrastructure.spec.ts`이고, 이
 * 파일은 동작만 본다.
 */
import { expect, test, type Page } from "@playwright/test";

import {
  backtest,
  currentSource,
  editor,
  expectPhase,
  GOLDEN,
  mustReplace,
  openEditor,
  replaceSource,
  saveAndWaitForRevision,
  strategyIdentity,
} from "./workbench-helpers";

/** 대본 공급자는 키를 검사하지 않는다. 꼬리 4자리만 화면에 남는지 보려고 끝을 알아보게 둔다. */
const SECRET = "sk-scripted-e2e-key-7431";
const SECRET_TAIL = "7431";
const PROVIDER_LABEL = "대본 Claude";

/** 대본이 제안하는 제목(`_scenarios.py`). 적용이 실제로 문서를 바꿨는지 보는 표식이다. */
const PROPOSED_TITLE = "KRX 12-1 모멘텀";

/**
 * IDE 우측 패널. 패널(`widgets/strategy-ide`)과 그 안의 채팅 사이드바(`features/assist-strategy`)가
 * 둘 다 "AI 어시스턴트"라는 이름의 `aside`라서 바깥 것을 집는다 — 안쪽은 그 안에 들어 있다.
 */
const assistant = (page: Page) =>
  page.getByRole("complementary", { name: "AI 어시스턴트" }).first();

const composer = (page: Page) =>
  assistant(page).getByRole("textbox", { name: "어시스턴트에게 보낼 메시지" });

const transcript = (page: Page) =>
  assistant(page).getByRole("log", { name: "대화 내용" });

/** 상단 바의 토글. 이미 열려 있으면 그 버튼이 없으므로(패널이 접기 버튼을 가진다) 세지 않는다. */
const openAssistant = async (page: Page) => {
  const toggle = page.getByRole("button", { name: "AI 어시스턴트", exact: true });
  if ((await toggle.count()) > 0) await toggle.click();
  await expect(composer(page)).toBeVisible();
};

const ask = async (page: Page, text: string) => {
  await composer(page).fill(text);
  await assistant(page).getByRole("button", { name: "보내기" }).click();
};

/** 전 시나리오가 같은 문서에서 돌지 않도록, 테스트마다 자기 리비전을 만든다. */
const saveStrategyRevision = async (page: Page, title: string) => {
  await openEditor(page, "/research/strategies/new");
  await replaceSource(page, mustReplace(GOLDEN, "퀄리티 모멘텀", title));
  await expectPhase(page, "검증 통과");
  await saveAndWaitForRevision(page, 1);
  return strategyIdentity(page);
};

/**
 * 설정 화면에 활성 공급자 하나를 보장한다. 대본 공급자라 probe는 언제나 통과한다.
 *
 * 이미 있으면 다시 만들지 않는다 — 이 project의 backend DB는 테스트 사이에 살아 있어서, 매번
 * 등록하면 같은 이름의 카드가 쌓이고 그다음 조회가 둘을 집는다. 그러면서도 테스트 하나만 단독으로
 * 돌릴 때는 스스로 만든다.
 */
const ensureProvider = async (page: Page, label = PROVIDER_LABEL) => {
  const response = await page.goto("/settings");
  expect(response?.ok()).toBe(true);
  const section = page.getByRole("region", { name: "AI 어시스턴트 공급자" });
  // 추가 폼은 공급자 조회가 끝나야 그려진다 — 목록이 비었는지 여기서부터 물을 수 있다.
  await expect(section.getByLabel("표시 이름")).toBeVisible();
  if ((await section.getByTestId("provider-active").count()) > 0) return section;
  await section.getByLabel("표시 이름").fill(label);
  await section.getByLabel("API 키").fill(SECRET);
  await section.getByRole("button", { name: "연결 테스트 후 저장" }).click();
  await expect(section.getByRole("heading", { name: label })).toBeVisible();
  return section;
};

test.describe("AI 어시스턴트", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(300_000);

  test("설정에서 공급자를 등록하면 활성이 되고 키는 꼬리 4자리만 남는다", async ({
    page,
  }) => {
    const section = await ensureProvider(page);

    // 대본 공급자는 두 종류를 모두 채운다 — 사용자가 어느 쪽을 골라도 같은 대본이 돈다.
    await expect(section.getByTestId("provider-kind-anthropic")).toContainText(
      "사용 가능",
    );
    await expect(section.getByTestId("provider-kind-openai")).toContainText(
      "사용 가능",
    );

    const card = section
      .getByRole("listitem")
      .filter({ has: page.getByRole("heading", { name: PROVIDER_LABEL }) });
    // 배지 텍스트에는 색 없이도 읽히도록 숨은 상태 낱말이 앞에 붙는다 — testid로 집는다.
    await expect(card.getByTestId("provider-active")).toBeVisible();
    await expect(card).toContainText(`••••${SECRET_TAIL}`);

    // 키는 요청 본문으로만 갔다. 화면에도 입력칸에도 남지 않는다(spec D7).
    await expect(section.getByLabel("API 키")).toHaveValue("");
    expect(await page.content()).not.toContain(SECRET);

    await card.getByRole("button", { name: "연결 테스트" }).click();
    await expect(card.getByRole("status")).toContainText("연결 확인됨");
    expect(await page.content()).not.toContain(SECRET);
  });

  test("사이드바 질문에 답이 스트리밍되고 새로고침해도 이력과 진행 중 턴이 이어진다", async ({
    page,
  }) => {
    await ensureProvider(page);
    const { strategyId } = await saveStrategyRevision(page, "B-05 스트리밍");
    await openAssistant(page);

    await ask(page, "모멘텀 전략이 뭐야?");
    await expect(transcript(page)).toContainText("모멘텀 전략이 뭐야?");
    await expect(transcript(page)).toContainText(
      "최근 많이 오른 종목을 사는 전략입니다.",
    );
    await expect(
      assistant(page).getByText("답변이 완료되었습니다.", { exact: true }),
    ).toBeVisible();

    // 새로고침: 사이드바 열림은 배치에 남고 대화는 서버 이력에서 돌아온다.
    await page.reload();
    await expect(editor(page)).toBeVisible();
    await expect(transcript(page)).toContainText(
      "최근 많이 오른 종목을 사는 전략입니다.",
    );

    // 진행 중 턴 재연결(spec D7): 긴 답변을 시작해 두고 도중에 새로고침한다.
    await ask(page, "모멘텀 전략을 천천히 설명해 줘");
    await expect(transcript(page)).toContainText("천천히 설명하겠습니다.");
    await page.reload();
    await expect(editor(page)).toBeVisible();
    await expect(
      assistant(page).getByText("답변을 작성하는 중입니다.", { exact: true }),
    ).toBeVisible();
    await expect(transcript(page)).toContainText(
      "한 종목이 성과를 좌우하지 않게 합니다.",
    );
    await expect(
      assistant(page).getByText("답변이 완료되었습니다.", { exact: true }),
    ).toBeVisible();

    // 취소: 답이 흘러나오는 중에 멈춘다.
    await ask(page, "이번에는 천천히 한 번 더 설명해 줘");
    // 같은 대본을 두 번 돌리므로 대화 전체가 아니라 **마지막 턴 블록**만 본다. 전체를 보면 앞
    // 턴의 같은 문장에 걸려 기다리지 않고 지나간다.
    await expect(transcript(page).getByRole("article").last()).toContainText(
      "천천히 설명하겠습니다.",
    );
    const stop = assistant(page).getByRole("button", { name: "중지" });
    await expect(stop).toBeVisible();
    await stop.click();
    // 턴이 끝나 입력이 다시 열린다.
    await expect(
      assistant(page).getByRole("button", { name: "보내기" }),
    ).toBeVisible();
    await expect(stop).toHaveCount(0);

    // 취소 사유는 서버 이력에 남아 있고 다시 열면 보인다. **지금은 취소한 화면에서 바로 보이지
    // 않는다** — 취소 응답이 턴을 종료 상태로 바꾸는 순간 사이드바가 스트림을 닫아, 그 뒤에
    // 서버가 append하는 `Failure(CANCELLED)`를 받지 못한다(B-05 보고 DEFECT-B05-001). 이 단언은
    // 그 경계를 그대로 고정한다: 고쳐서 즉시 보이게 되면 아래 reload 없이도 통과해야 한다.
    await page.reload();
    await expect(editor(page)).toBeVisible();
    await expect(transcript(page).getByRole("article").last()).toContainText(
      "요청을 취소했습니다.",
    );

    expect(page.url()).toContain(`/research/strategies/${strategyId}/`);
  });

  test("제안 카드를 문서에 적용하면 검증을 통과하고 적용 후 백테스트가 실행으로 간다", async ({
    page,
  }) => {
    await ensureProvider(page);
    await saveStrategyRevision(page, "B-05 제안 적용");
    const before = await currentSource(page);
    await openAssistant(page);

    await ask(page, "KRX에서 통할 만한 모멘텀 전략 하나 제안해 줘");

    const card = assistant(page).getByRole("article", {
      name: PROPOSED_TITLE,
    });
    await expect(card).toBeVisible({ timeout: 60_000 });
    // 제안의 검증 결과는 서버가 채운 값이다. 프론트가 다시 판정하지 않는다(SoT 규칙).
    await expect(card).toContainText("검증 통과");
    // 출처는 스킴을 확인한 것만 링크가 되고 도착지 호스트를 늘 함께 보인다(B-03 리뷰 P2).
    const source = card.getByRole("link", { name: "KRX 모멘텀 리뷰" });
    await expect(source).toHaveAttribute(
      "href",
      "https://example.com/krx-momentum",
    );
    await expect(source).toHaveAttribute("rel", "noopener noreferrer");
    await expect(card).toContainText("도착지 example.com");

    // 미리보기는 문서를 건드리지 않고 차이만 보인다.
    await card.getByRole("button", { name: "미리보기" }).click();
    const preview = page.getByRole("dialog", { name: "제안 미리보기" });
    await expect(
      preview.getByRole("table", { name: "제안과 현재 문서의 차이" }),
    ).toContainText(PROPOSED_TITLE);
    await preview.getByRole("button", { name: "취소" }).click();
    expect(await currentSource(page)).toBe(before);

    // 적용: 기준 텍스트가 그대로라 확인 없이 전체 범위 교체 한 번으로 들어간다(spec D7).
    await card.getByRole("button", { name: "문서에 적용" }).click();
    await expect(page.getByText("제안을 문서에 적용했습니다.")).toBeVisible();
    const applied = await currentSource(page);
    expect(applied).toContain(`title: "${PROPOSED_TITLE}"`);
    expect(applied).not.toBe(before);
    await expectPhase(page, "검증 통과");

    // "적용 후 백테스트": 이제 문서가 제안 기준과 달라졌으므로 확인을 거쳐 덮어쓴다.
    await card.getByRole("button", { name: "적용 후 백테스트" }).click();
    const confirm = page.getByRole("dialog", { name: "문서가 바뀌었습니다" });
    await expect(confirm).toBeVisible();
    await confirm.getByRole("button", { name: "그래도 덮어쓰기" }).click();

    // 저장하지 않은 문서를 떠나므로 이탈 확인을 거친다.
    const leaveGuard = page.getByRole("button", { name: "나가기" });
    await expect(leaveGuard).toBeVisible({ timeout: 60_000 });
    await leaveGuard.click();

    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    await expect(
      page.getByRole("status", { name: "실행 상태" }),
    ).toContainText("completed", { timeout: 180_000 });
    await expect(
      page.getByRole("article", { name: "백테스트 결과" }),
    ).toBeVisible({ timeout: 180_000 });
  });

  test("검색 출처를 링크로 보이고 검증에 실패한 턴은 실패 문구로 끝난다", async ({
    page,
  }) => {
    await ensureProvider(page);
    await saveStrategyRevision(page, "B-05 검색 실패");
    await openAssistant(page);

    await ask(page, "요즘 KRX 팩터 성과를 검색해서 알려 줘");

    await expect(transcript(page)).toContainText("웹 검색");
    await expect(transcript(page)).toContainText("KRX 모멘텀 팩터 2026");
    for (const [title, host] of [
      ["KRX 모멘텀 리뷰", "example.com"],
      ["팩터 성과 보고", "example.com"],
    ] as const) {
      const link = transcript(page).getByRole("link", { name: title });
      await expect(link).toHaveAttribute("target", "_blank");
      await expect(link).toHaveAttribute("rel", "noopener noreferrer");
      await expect(transcript(page)).toContainText(`도착지 ${host}`);
    }

    // 3회 연속 검증 실패로 턴이 끝난다. 거절된 제안은 카드가 되지 않는다.
    await expect(transcript(page)).toContainText(
      "모델이 낸 전략이 검증을 통과하지 못했습니다.",
      { timeout: 60_000 },
    );
    await expect(
      assistant(page).getByRole("article", { name: PROPOSED_TITLE }),
    ).toHaveCount(0);

    // 실패한 턴은 문서를 건드리지 않는다 — 실행 게이트도 그대로다.
    await expectPhase(page, "저장됨");
    await expect(backtest(page)).toBeEnabled();
  });
});
