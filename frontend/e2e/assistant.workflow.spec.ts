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
 * 대본은 질문에 든 낱말로 고른다(`_scenarios.py`의 `SCENARIO_KEYWORDS`): "창을 줄"이면 팩터 창을
 * 줄인 제안, "제안"이면 도구 호출 후 제목만 바꾼 제안, "검색"이면 검색 활동 후 검증 3회 실패,
 * "천천히"면 긴 스트리밍, 그 밖이면 짧은 답변이다.
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
/** 대본 공급자가 기본 모델 이름에 박아 두는 고정 표식(`_adapter.py`의 `SCRIPTED_MARKER`). */
const SCRIPTED_MARKER = "scripted fake";

/** 대본이 제안하는 제목(`_scenarios.py`). 적용이 실제로 문서를 바꿨는지 보는 표식이다. */
const PROPOSED_TITLE = "KRX 12-1 모멘텀";
/** 팩터 창을 줄이는 대본의 제목과 새 창(`_scenarios.py`의 `factor_window_proposal`). */
const WINDOW_PROPOSED_TITLE = "KRX 6개월 모멘텀";
const PROPOSED_WINDOW = 126;

/**
 * IDE 우측 패널. landmark·이름·제목은 슬롯(`widgets/strategy-ide`)이 소유하고 채팅 feature는 이름
 * 없는 `<section>`이라, "AI 어시스턴트"라는 이름의 landmark는 이 하나뿐이다(B-04 슬롯 계약).
 */
const assistant = (page: Page) =>
  page.getByRole("complementary", { name: "AI 어시스턴트" });

const composer = (page: Page) =>
  assistant(page).getByRole("textbox", { name: "어시스턴트에게 보낼 메시지" });

const transcript = (page: Page) =>
  assistant(page).getByRole("log", { name: "대화 내용" });

/** 상주하는 진행 상태 영역. 내용과 함께 삽입되지 않고 문구만 바뀐다(B-03 3차 리뷰 P2). */
const progress = (page: Page) =>
  assistant(page).getByRole("status", { name: "진행 상태" });

/**
 * 사이드바를 펼친다. 기본이 접힘이고 **내용은 첫 펼침 이후에 마운트되므로**(B-04 리뷰 P3: 화면을
 * 열 때마다 어시스턴트 질의가 나가지 않게) 모든 시나리오는 여기서 시작한다.
 *
 * 새로고침 뒤에는 배치에 남은 열림 상태로 이미 펼쳐져 있고 그때는 상단 바 토글이 사라진다 —
 * 그래서 버튼이 있을 때만 누른다.
 */
const openAssistant = async (page: Page) => {
  const toggle = page.getByRole("button", {
    name: "AI 어시스턴트",
    exact: true,
  });
  if ((await toggle.count()) > 0) await toggle.click();
  await expect(
    assistant(page).getByRole("heading", { name: "AI 어시스턴트" }),
  ).toBeVisible();
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
  if ((await section.getByTestId("provider-active").count()) > 0)
    return section;
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
    // 모델을 비워 등록했으므로 공급자의 기본 모델이 쓰인다. 대본 공급자는 그 자리에 자기가
    // 가짜라고 적어 둔다 — 켠 채로 배포됐을 때 화면에서 알아챌 유일한 자리다(리뷰 R1-004).
    await expect(card).toContainText(SCRIPTED_MARKER);

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
    await expect(progress(page)).toHaveText("답변이 완료되었습니다.");

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
    await expect(progress(page)).toHaveText("답변을 작성하는 중입니다.");
    await expect(progress(page)).toHaveText("답변이 완료되었습니다.", {
      timeout: 60_000,
    });
    // 재연결은 멱등해야 한다: 끊기기 전에 받은 조각을 다시 받아도 본문이 늘어나지 않는다.
    // `toContainText`로는 중복이 보이지 않으므로 **정확 일치**로 못 박는다(리뷰 R1-005).
    await expect(
      transcript(page).getByRole("article").last().locator("p.assist-text"),
    ).toHaveText([
      "모멘텀 전략을 천천히 설명해 줘",
      "천천히 설명하겠습니다. 모멘텀 전략은 최근 수익률이 높았던 종목이 당분간 더 오르는 경향에 기대는 전략입니다. KRX에서도 이 경향은 관측됩니다. 다만 그대로 쓰지는 않습니다. 12개월 수익률에서 최근 1개월을 빼는 형태를 많이 씁니다. 직전 한 달은 되돌림이 잦기 때문입니다. 순위를 매긴 뒤에는 상위 몇 종목을 담을지 정합니다. 종목 수가 적으면 변동이 커지고 많으면 지수에 가까워집니다. 리밸런싱 주기도 같이 봅니다. 자주 갈아탈수록 수수료와 슬리피지가 쌓입니다. 마지막으로 종목당 비중 상한을 두어 한 종목이 성과를 좌우하지 않게 합니다.",
    ]);

    // 취소(스트리밍 도중): 답이 흘러나오는 중에 멈추면 사유가 그 자리에서 말풍선에 남는다.
    // 진행 상태 영역은 상주하며 문구만 바뀐다(B-03 3차 리뷰 P2).
    await ask(page, "이번에는 천천히 한 번 더 설명해 줘");
    // 같은 대본을 두 번 돌리므로 대화 전체가 아니라 **마지막 턴 블록**만 본다. 전체를 보면 앞
    // 턴의 같은 문장에 걸려 기다리지 않고 지나간다.
    const midStream = transcript(page).getByRole("article").last();
    await expect(midStream).toContainText("천천히 설명하겠습니다.");
    const stop = assistant(page).getByRole("button", { name: "중지" });
    await expect(stop).toBeVisible();
    await stop.click();
    // 턴이 끝나 입력이 다시 열리고, 상주하는 진행 상태가 중지를 알린다.
    await expect(
      assistant(page).getByRole("button", { name: "보내기" }),
    ).toBeVisible();
    await expect(stop).toHaveCount(0);
    await expect(progress(page)).toHaveText("답변을 중지했습니다.");
    // 새로고침 없이 사유가 보인다: 사이드바가 종료 이벤트까지 스트림을 열어 둔다(B-02).
    await expect(midStream).toContainText("요청을 취소했습니다.");
    // 받다 만 조각은 남고 그 뒤는 오지 않는다 — 이미 스트리밍된 텍스트는 보존한다(spec D3).
    await expect(midStream).not.toContainText(
      "한 종목이 성과를 좌우하지 않게 합니다.",
    );

    // 여기까지는 **이 화면이 그린 것**이라 서버가 취소를 정말 받아들였는지는 아직 모른다
    // (리뷰 R2-001: 마지막 문장 부재는 취소 직후라면 무시당해도 참이다). 서버 쪽 사실 두 개를
    // 따로 본다.
    //
    // 1) 사유가 **이력에** 남았다. 새로고침한 화면은 `GET /sessions/{id}`가 돌려준 것만 그리므로,
    //    여기 문구가 보인다는 것은 `Failure(CANCELLED)`가 저장됐다는 뜻이다(A-07).
    await page.reload();
    await expect(editor(page)).toBeVisible();
    await expect(transcript(page).getByRole("article").last()).toContainText(
      "요청을 취소했습니다.",
    );
    // 2) 세션 슬롯이 풀렸다. 러너는 스레드가 끝날 때까지 슬롯을 쥐고 그동안 새 턴을 409
    //    `turn_in_progress`로 거절하므로, 다음 질문이 받아들여진다는 것은 그 턴이 실제로
    //    끝났다는 뜻이다. 거절당하면 입력칸에 질문이 되돌아오고 아래 단언이 깨진다.

    // 취소(보내자마자): 앞 턴이 끝났으므로 이 질문은 거절 없이 받아들여진다(위 2번). 버튼이 뜨는
    // 즉시 멈춰도 사유가 남는다. 공급자가 아무 이벤트도 내기 전에
    // 반환하면 서비스 루프가 한 번도 돌지 않아 사유가 비는 갈래가 있었고, A-07 `5009a03c`가
    // 루프 진입 여부와 무관하게 사유를 세우도록 닫았다(DEFECT-AI-B05-001의 두 번째 갈래).
    //
    // 첫 조각이 오기 **전**이라는 것까지 브라우저에서 못 박지는 않는다 — 턴 시작 응답과 첫 조각
    // 사이는 밀리초라 클릭이 어느 쪽에 떨어질지 정할 수 없다. 그 경계는 application 단위 테스트가
    // 결정적으로 고정하고, 여기서는 "언제 눌러도 사유가 남는다"를 본다.
    await ask(page, "천천히 한 번만 더 설명해 줘");
    await expect(
      assistant(page).getByRole("button", { name: "중지" }),
    ).toBeVisible();
    await expect(assistant(page).getByRole("alert")).toHaveCount(0);
    await assistant(page).getByRole("button", { name: "중지" }).click();
    await expect(
      assistant(page).getByRole("button", { name: "보내기" }),
    ).toBeVisible();
    await expect(transcript(page).getByRole("article").last()).toContainText(
      "요청을 취소했습니다.",
    );

    expect(page.url()).toContain(`/research/strategies/${strategyId}/`);
  });

  test("제안 카드를 미리 보고 적용한 뒤 적용 후 백테스트가 실행 화면까지 간다", async ({
    page,
  }) => {
    await ensureProvider(page);
    await saveStrategyRevision(page, "B-05 제안 적용");
    const before = await currentSource(page);
    await openAssistant(page);
    // 펼치는 순간 상단 토글이 스스로 언마운트되므로 패널이 포커스를 받는다(B-04 3차 리뷰 P1-1).
    // 그러지 않으면 키보드 사용자는 방금 연 사이드바를 찾아 처음부터 탭해야 한다.
    await expect(assistant(page)).toBeFocused();

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
    await expect(source).toHaveAttribute("target", "_blank");
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
    // 적용이 문서의 나머지를 건드리지 않았다. 대본이 제목 줄만 바꾸기로 했으므로(`_scenarios.py`)
    // 그 밖의 줄이 하나라도 달라지면 전체 범위 교체가 뭔가를 더 지운 것이다(리뷰 R1-006).
    const beforeLines = before.split("\n");
    const appliedLines = applied.split("\n");
    expect(appliedLines).toHaveLength(beforeLines.length);
    const changedLines = appliedLines.filter(
      (line, index) => line !== beforeLines[index],
    );
    expect(changedLines).toEqual([`title: "${PROPOSED_TITLE}"`]);
    await expectPhase(page, "검증 통과");

    // "적용 후 백테스트": 문서가 제안 기준과 달라졌으므로 확인을 거쳐 덮어쓴다.
    await card.getByRole("button", { name: "적용 후 백테스트" }).click();
    const confirm = page.getByRole("dialog", { name: "문서가 바뀌었습니다" });
    await expect(confirm).toBeVisible();
    await confirm.getByRole("button", { name: "그래도 덮어쓰기" }).click();

    // 같은 제안을 두 번째로 적용한 것이라 바뀐 내용이 없다. 그 사실을 알리고 실행은 그대로
    // 이어진다 — 편집기 change가 없다고 결과와 체인이 함께 사라지면 안 된다(B-04 3차 리뷰 P2-1).
    await expect(
      page.getByText("제안이 지금 문서와 같아 바뀐 내용이 없습니다."),
    ).toBeVisible();

    // 저장하지 않은 문서를 떠나므로 이탈 확인을 거친다.
    const leaveGuard = page.getByRole("button", { name: "나가기" });
    await expect(leaveGuard).toBeVisible({ timeout: 60_000 });
    await leaveGuard.click();

    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
      "completed",
      { timeout: 180_000 },
    );
    await expect(
      page.getByRole("article", { name: "백테스트 결과" }),
    ).toBeVisible({ timeout: 180_000 });
  });

  test("팩터 그래프를 바꾸는 제안도 적용 후 백테스트가 팩터 계획 조회를 기다려 실행한다", async ({
    page,
  }) => {
    // C-02 리뷰 P1-1. 그래프가 바뀌면 compile 직후 새 팩터 계획(explain)을 조회하는 동안 실행
    // 게이트가 닫힌다. 그 닫힘은 "아직 검증 중"이라 체인이 기다렸다가 계획이 오면 실행해야 한다.
    // 위 시나리오는 제목만 바꿔 계획이 캐시에 있으므로 이 경로를 밟지 않는다.
    await ensureProvider(page);
    await saveStrategyRevision(page, "C-02 팩터 창 제안");
    await openAssistant(page);

    await ask(page, "모멘텀 창을 줄인 안을 제안해 줘");
    const card = assistant(page).getByRole("article", {
      name: WINDOW_PROPOSED_TITLE,
    });
    await expect(card).toBeVisible({ timeout: 60_000 });
    await expect(card).toContainText("검증 통과");

    // 캐시에 없는 새 그래프의 계획 조회가 실제로 나가는지 함께 본다 — 그래야 이 시나리오가
    // "조회 중 닫힘" 경로를 밟았다고 말할 수 있다.
    const freshExplain = page.waitForRequest(
      (request) =>
        request.method() === "POST" &&
        request.url().includes("/api/v1/factors/explain") &&
        (request.postData() ?? "").includes(`"window":${PROPOSED_WINDOW}`),
    );
    // 턴을 시작한 뒤 문서를 고치지 않았으므로 확인 창 없이 바로 적용된다.
    await card.getByRole("button", { name: "적용 후 백테스트" }).click();
    // 새 창(126)이 실린 계획 조회가 나갔다는 것 자체가 적용된 문서가 그래프를 바꿨다는 증거다. 편집기를
    // 여기서 다시 읽지 않는다 — 실행이 곧바로 이어지면 이탈 확인 창이 편집기를 가린다.
    await freshExplain;

    // 계획이 도착하면 실행이 시작되고, 저장하지 않은 문서를 떠나므로 이탈 확인이 뜬다.
    const leaveGuard = page.getByRole("button", { name: "나가기" });
    await expect(leaveGuard).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText(/백테스트를 시작하지 않았습니다/u)).toHaveCount(
      0,
    );
    await leaveGuard.click();

    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
      "completed",
      { timeout: 180_000 },
    );
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
