/**
 * 정동민의 스토리 e2e — 화면의 말과 오류 문장을 쉬운 한글로 읽는다(US-DM-06).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다. 이름·설명 키와 연산자
 * 목록은 backend가, 문장은 frontend i18n(화면 어휘)과 backend(진단 문장)가 소유한다. 여기서는 영어
 * 식별자를 모르는 사람이 화면에서 무엇을 읽는지만 본다. 레이아웃 계약(노드 카드 본문 위치 등)은
 * `workbench.workflow.spec.ts`의 P1-04 테스트가 소유하므로 다시 재지 않는다.
 */
import { expect, test } from "@playwright/test";

import {
  expectPhase,
  GOLDEN,
  mustReplace,
  openEditor,
  replaceSource,
  saveAndWaitForRevision,
} from "../workbench-helpers";

test(
  "US-DM-06 그래프 편집 화면은 노드 종류·연산자·필드를 한글 이름과 설명으로 보이고 삭제 거부를 노드 이름으로 말한다",
  { tag: ["@story", "@US-DM-06"] },
  async ({ page }) => {
    await openEditor(page, "/research/strategies/new");
    await replaceSource(
      page,
      mustReplace(GOLDEN, "퀄리티 모멘텀", "US-DM-06 한글 화면"),
    );
    await expectPhase(page, "검증 통과");
    await page.getByRole("tab", { name: "Graph", exact: true }).click();
    const editor = page.getByRole("region", { name: "그래프 편집" });

    // 노드 종류: 카드에 한글 이름이 먼저 보이고, 영어 kind는 보조 코드 표기로만 남는다.
    const momentumNode = editor.getByRole("button", {
      name: "노드 편집: mom_252",
    });
    await expect(momentumNode).toContainText("기간 집계");
    await expect(momentumNode.getByRole("code")).toHaveText("time_series");

    // 연산자: 팔레트가 한글 이름과 한 줄 설명·계산식을 보인다.
    const palette = editor.getByRole("group", { name: "연산자 팔레트" });
    const movingAverage = palette.getByRole("listitem").filter({
      has: page.getByRole("button", {
        name: "기간 평균 노드 추가",
        exact: true,
      }),
    });
    await expect(movingAverage).toContainText("이동평균이 이것입니다");

    // 필드: 노드를 고르면 설정 칸이 한글 라벨(보조로 키)과 한 줄 설명을 보인다.
    await momentumNode.click();
    const selected = editor.getByRole("group", { name: /선택한 노드/u });
    await expect(
      selected.getByRole("spinbutton", { name: "집계 기간 window" }),
    ).toHaveValue("252");
    await expect(selected).toContainText("집계에 쓸 세션 수입니다");
    // 노드 종류의 한 줄 설명은 계약 패널이 한글로 보인다.
    const contract = page.getByRole("complementary", { name: "계약" });
    await expect(
      contract.getByRole("heading", { name: "기간 집계" }),
    ).toBeVisible();
    await expect(contract).toContainText(
      "같은 종목의 과거 구간을 값 하나로 집계합니다.",
    );

    // 삭제 거부: 참조하는 노드를 JSON Pointer가 아니라 노드 이름으로 말한다.
    await editor.getByRole("button", { name: "close · 삭제" }).click();
    const refusal = editor.getByRole("alert");
    await expect(refusal).toHaveText(
      "close을(를) 다른 곳이 참조하고 있어 삭제하지 않았습니다: mom_252",
    );
    await expect(refusal).not.toContainText("/factors/");
    // 거부된 삭제는 문서를 건드리지 않는다.
    await expectPhase(page, "검증 통과");
  },
);

test(
  "US-DM-06 필드 이름을 틀리거나 1.0 문법을 쓰면 문제 목록이 한글로 고칠 방법을 말하고 업그레이드를 안내한다",
  { tag: ["@story", "@US-DM-06"] },
  async ({ page }) => {
    const valid = mustReplace(GOLDEN, "퀄리티 모멘텀", "US-DM-06 오류 문장");
    await openEditor(page, "/research/strategies/new");
    await replaceSource(page, valid);
    await expectPhase(page, "검증 통과");
    // 업그레이드 배너는 저장된 리비전 화면에 있다. 저장해 두고 그 화면에서 고쳐 쓴다.
    await saveAndWaitForRevision(page, 1);
    const problems = page.getByRole("region", { name: "문제" });

    // 필드 이름을 틀리면: 한글 문장이 무엇이 틀렸는지와 가까운 올바른 이름을 말한다.
    await replaceSource(
      page,
      mustReplace(valid, "max_name_weight", "max_name_wieght"),
    );
    await expectPhase(page, "구조 오류");
    await expect(problems).toContainText("모르는 키입니다");
    await expect(problems).toContainText("혹시 `max_name_weight`인가요?");

    // 1.0에서만 쓰던 키를 적으면: 문장이 그 사실과 할 일(지우거나 업그레이드)을 말하고, 화면이
    // 업그레이드 안내를 띄운다. schema 1.2(P2-03)에는 `execution` 절이 없어 1.0 의
    // `signal.method` 를 쓴다.
    await replaceSource(
      page,
      mustReplace(
        valid,
        "portfolio:\n",
        "signal:\n  method: weighted_sum\nportfolio:\n",
      ),
    );
    await expectPhase(page, "구조 오류");
    await expect(problems).toContainText("1.0에서만 쓰던 키입니다");
    await expect(problems).toContainText("업그레이드하세요");
    const upgrade = page.getByRole("region", { name: "schema 1.0 문서" });
    await expect(upgrade).toBeVisible();
    await expect(
      upgrade.getByRole("button", { name: "1.1로 업그레이드" }),
    ).toBeVisible();
  },
);
