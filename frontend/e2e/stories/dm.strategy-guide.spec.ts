/**
 * 정동민의 스토리 e2e — 새 전략을 단계별 안내를 따라 만든다(US-DM-13, 보드 04b).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다. 안내는 전략 문서·편집기를
 * 바꾸지 않으므로, 안내로 만든 문서와 같은 내용을 손으로 쓴 문서를 실제 backend compile 에 넣어 `spec_hash`
 * 가 같은지 본다. 팩터 이름·분류·가용성은 실제 카탈로그 응답에서 읽고 테스트에 적지 않는다.
 */
import { expect, test } from "@playwright/test";

import {
  compileStrategyDocument,
  getFactorCatalog,
  type FactorDefinition,
} from "../../src/shared/api/generated";
import { ensureProvider } from "../assistant-helpers";
import { currentSource } from "../editor-helpers";
import { apiClient, requireData } from "../workbench-helpers";

const TITLE = "안내로 만든 모멘텀";
/** 대본이 제안하는 전략 제목(`_scenarios.py`의 `_IDEA_TITLE`). */
const IDEA_TITLE = "KRX 대형 모멘텀";

test(
  "US-DM-13 새 전략을 단계별로 고르면 같은 전략 문서가 만들어지고 편집기에서 이어 쓴다",
  { tag: ["@story", "@US-DM-13"] },
  async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    const catalog = requireData(
      (await getFactorCatalog({ client: apiClient, query: { page_size: 100 } })).data,
      "factor catalog",
    );
    const factors: FactorDefinition[] = catalog.factors;
    const ready = factors.find((factor) => factor.availability === "implemented");
    const soon = factors.find((factor) => factor.availability === "catalog_only");
    expect(ready).toBeDefined();
    expect(soon).toBeDefined();
    const categoryName = (factor: FactorDefinition) =>
      new RegExp(
        `^${{ price: "가격", financial: "재무", consensus: "컨센서스", flow: "수급", short: "공매도", credit: "신용", event: "이벤트" }[factor.category]} \\d+$`,
        "u",
      );

    // 목록의 "새 전략 만들기"는 안내로 연다.
    await page.goto("/research/strategies");
    await page.getByRole("link", { name: "새 전략 만들기" }).first().click();
    await expect(page).toHaveURL(/guide=true/u);
    const steps = page.getByRole("navigation", { name: "만드는 단계" });
    // 단계 이름 뒤에는 상태(오류 N·완료)가 붙을 수 있다. 빈 이름은 처음부터 오류다.
    await expect(steps.getByRole("button")).toHaveText([
      /^1이름·의도/u,
      /^2유니버스/u,
      /^3알파 팩터/u,
      /^4결합/u,
      /^5포트폴리오 구성/u,
      /^6리스크 제약/u,
      /^7확인/u,
    ]);
    await expect(steps.getByRole("button", { name: "이름·의도", exact: true })).toHaveAttribute(
      "aria-current",
      "step",
    );
    await expect(page.getByRole("button", { name: "편집기에서 직접 쓰기" })).toBeVisible();
    // 오른쪽 칸은 늘 문서 미리보기다(AI 도우미는 화면 위에 떠 있는 창이다).
    // 다른 시나리오가 공급자를 연결해 두었으면 AI 창이 떠 있다. 이 시나리오는 단계 조작만 보므로 최소화한다.
    const minimize = page.getByRole("button", { name: "AI 도우미 최소화" });
    if (await minimize.isVisible()) await minimize.click();
    const preview = page.getByRole("complementary", { name: "전략 문서 미리보기" });

    // 1단계: 이름을 적으면 오른쪽 문서에 바로 들어간다.
    const title = page.getByRole("textbox", { name: "전략 이름" });
    await title.fill(TITLE);
    await title.press("Tab");
    await expect(preview).toContainText(`title: ${TITLE}`);

    // 2단계는 고르지 않고 넘어간다. 3단계: 분류 칩과 분류별 개수, 준비 중 팩터는 못 담는다.
    await page.getByRole("button", { name: "다음" }).click();
    await page.getByRole("button", { name: "다음" }).click();
    const chips = page.getByRole("group", { name: "팩터 분류" });
    await chips.getByRole("button", { name: categoryName(soon!) }).click();
    await expect(
      chips.getByRole("button", { name: categoryName(soon!) }),
    ).toHaveAttribute("aria-pressed", "true");
    const blocked = page.getByRole("button", { name: `${soon!.label} · 담기` });
    await expect(blocked).toBeDisabled();
    await expect(blocked).toHaveAccessibleDescription("계산식이 아직 없어 담을 수 없어요.");

    await chips.getByRole("button", { name: categoryName(ready!) }).click();
    await page.getByRole("button", { name: `${ready!.label} · 담기` }).click();
    await expect(preview).toContainText(`factor_id: ${ready!.factor_id}`);
    await expect(preview.getByText("방금 바뀐 줄:").first()).toBeAttached();
    await page.screenshot({
      path: testInfo.outputPath("strategy-guide-factors.png"),
      fullPage: true,
    });

    // 결합·포트폴리오·리스크는 기본값 그대로 넘어간다. 기본값 칸은 문서에 쓰이지 않는다.
    for (let index = 0; index < 4; index += 1)
      await page.getByRole("button", { name: "다음" }).click();
    await expect(steps.getByRole("button", { name: "확인" })).toHaveAttribute(
      "aria-current",
      "step",
    );
    await expect(preview).not.toContainText("portfolio:");
    await expect(preview).not.toContainText("risk:");

    // 같은 초안을 편집기 그래프 보기로 연다.
    await page.getByRole("button", { name: "편집기에서 열기" }).click();
    await expect(page).not.toHaveURL(/guide=true/u);
    await expect(page.getByRole("region", { name: "전략 파이프라인" })).toBeVisible();
    await page.getByRole("tab", { name: "YAML", exact: true }).click();
    const guided = await currentSource(page);
    expect(guided).toContain(`title: ${TITLE}`);

    // 같은 내용을 편집기에서 손으로 쓴 문서(키 순서가 다르고 기본값 weight 를 생략했다)와 spec_hash 가 같다.
    const handWritten = JSON.stringify({
      factors: [
        {
          graph: ready!.default_graph,
          direction: ready!.preference,
          label: ready!.label,
          factor_id: ready!.factor_id,
        },
      ],
      title: TITLE,
      schema_version: "1.2",
    });
    const compile = async (source: string) =>
      requireData(
        (
          await compileStrategyDocument({
            client: apiClient,
            body: { format: "yaml", source },
          })
        ).data,
        "compile",
      );
    const [fromGuide, fromHand] = await Promise.all([
      compile(guided),
      compile(handWritten),
    ]);
    expect(fromGuide.spec_hash).not.toBeNull();
    expect(fromGuide.spec_hash).toBe(fromHand.spec_hash);
  },
);

test(
  "US-DM-13 안내 첫 단계부터 AI 도우미에게 만들 전략을 말하고 제안을 적용하면 단계 값이 채워진다",
  { tag: ["@story", "@US-DM-13"] },
  async ({ page }) => {
    test.setTimeout(120_000);
    await ensureProvider(page);
    await page.goto("/research/strategies/new?guide=true");

    // 공급자가 연결돼 있으면 AI 도우미는 화면 위에 떠 있는 창으로 열린다.
    const panel = page.getByRole("complementary", { name: "AI 도우미" });
    await expect(panel).toBeVisible();
    const input = panel.getByRole("textbox", { name: "어시스턴트에게 보낼 메시지" });
    await panel
      .getByRole("group", { name: "이 단계에서 자주 묻는 것" })
      .getByRole("button", { name: "어떤 전략을 만들지 같이 정해 주세요" })
      .click();
    // 추천 질문은 입력칸만 채우고 보내지 않는다.
    await expect(input).toHaveValue("어떤 전략을 만들지 같이 정해 주세요");

    // 대본 공급자는 "새 전략" 낱말로 전략 전체를 제안하는 대본을 고른다(`_scenarios.py`).
    await input.fill("최근 많이 오른 대형주를 사는 새 전략을 만들어 줘");
    await panel.getByRole("button", { name: "보내기" }).click();
    const card = panel.getByRole("article", { name: IDEA_TITLE });
    await expect(card).toBeVisible({ timeout: 60_000 });
    // 안내에는 실행 설정이 없어 "적용 후 백테스트"를 두지 않는다.
    await expect(card.getByRole("button", { name: "적용 후 백테스트" })).toHaveCount(0);
    await card.getByRole("button", { name: "문서에 적용" }).click();

    // 같은 문서가 바뀌므로 안내 칸과 단계 상태가 그대로 따라온다.
    await expect(page.getByRole("textbox", { name: /^전략 이름/u })).toHaveValue(IDEA_TITLE);
    const steps = page.getByRole("navigation", { name: "만드는 단계" });
    await expect(steps.getByRole("button", { name: "이름·의도", exact: true })).toHaveAccessibleDescription(
      "(완료)",
    );
  },
);
