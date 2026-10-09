/**
 * AI 어시스턴트 e2e가 함께 쓰는 헬퍼 — 설정 화면의 공급자 보장, 사이드바 열기·질문·대화 로케이터.
 * `assistant.workflow.spec.ts`와 유저 스토리 e2e(`stories/`)가 같은 접근성 이름을 보도록 한 곳에 둔다.
 * 공급자는 backend 대본 adapter다(`STRATEGY_WORKBENCH_ASSISTANT_FAKE_PROVIDER=1`, e2e README).
 */
import { expect, type Page } from "@playwright/test";

/** 대본 공급자는 키를 검사하지 않는다. 꼬리 4자리만 화면에 남는지 보려고 끝을 알아보게 둔다. */
export const SECRET = "sk-scripted-e2e-key-7431";
export const SECRET_TAIL = "7431";
export const PROVIDER_LABEL = "대본 Claude";
/**
 * IDE 우측 패널. landmark·이름·제목은 슬롯(`widgets/strategy-ide`)이 소유하고 채팅 feature는 이름
 * 없는 `<section>`이라, "AI 어시스턴트"라는 이름의 landmark는 이 하나뿐이다(B-04 슬롯 계약).
 */
export const assistant = (page: Page) =>
  page.getByRole("complementary", { name: "AI 어시스턴트" });

export const composer = (page: Page) =>
  assistant(page).getByRole("textbox", { name: "어시스턴트에게 보낼 메시지" });

export const transcript = (page: Page) =>
  assistant(page).getByRole("log", { name: "대화 내용" });

/** 상주하는 진행 상태 영역. 내용과 함께 삽입되지 않고 문구만 바뀐다(B-03 3차 리뷰 P2). */
export const progress = (page: Page) =>
  assistant(page).getByRole("status", { name: "진행 상태" });

/**
 * 사이드바를 펼친다. 기본이 접힘이고 **내용은 첫 펼침 이후에 마운트되므로**(B-04 리뷰 P3: 화면을
 * 열 때마다 어시스턴트 질의가 나가지 않게) 모든 시나리오는 여기서 시작한다.
 *
 * 새로고침 뒤에는 배치에 남은 열림 상태로 이미 펼쳐져 있고 그때는 상단 바 토글이 사라진다 —
 * 그래서 버튼이 있을 때만 누른다.
 */
export const openAssistant = async (page: Page) => {
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

export const ask = async (page: Page, text: string) => {
  await composer(page).fill(text);
  await assistant(page).getByRole("button", { name: "보내기" }).click();
};

/**
 * 설정 화면에 활성 공급자 하나를 보장한다. 대본 공급자라 probe는 언제나 통과한다.
 *
 * 이미 있으면 다시 만들지 않는다 — 이 project의 backend DB는 테스트 사이에 살아 있어서, 매번
 * 등록하면 같은 이름의 카드가 쌓이고 그다음 조회가 둘을 집는다. 그러면서도 테스트 하나만 단독으로
 * 돌릴 때는 스스로 만든다.
 */
export const ensureProvider = async (page: Page, label = PROVIDER_LABEL) => {
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
