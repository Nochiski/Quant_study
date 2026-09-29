/**
 * 한상목의 스토리 e2e — 예전 schema로 쓴 YAML을 새 전략 화면에 붙여 넣고 현재 버전으로 올려 저장·실행한다
 * (US-SM-07, #257).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/sm.md`다. 저장된 동결 revision에서
 * 올리는 흐름은 `workbench.workflow.spec.ts`가 본다. 여기서는 저장본이 없는 새 전략 화면에서도 같은 배너가
 * 뜨고, 업그레이드 응답의 실행 설정을 사용자가 채운 뒤 저장과 백테스트까지 가는지를 본다. 변환 규칙과
 * 현재 버전 문자열은 backend 소유라 응답 값과 비교하고 frontend에 적지 않는다.
 */
import { expect, test, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { getStrategyDocument } from "../../src/shared/api/generated";
import {
  apiClient,
  backtest,
  currentSource,
  expectPhase,
  openEditor,
  replaceSource,
  requestedEnvironment,
  requireData,
  save,
  saveAndWaitForRevision,
  strategyIdentity,
  upgradeBanner,
  upgradeButton,
  upgradeFromBanner,
} from "../workbench-helpers";

const ownDirectory = dirname(fileURLToPath(import.meta.url));

/** backend 소유 은퇴 버전 fixture 원문. frontend는 읽기만 한다(`frontend-testing.md`). */
const retiredFixture = (name: string): string =>
  readFileSync(
    resolve(
      ownDirectory,
      "../../../backend/tests/fixtures/strategy_documents",
      name,
    ),
    "utf8",
  ).replace(/\r\n?/gu, "\n");

test(
  "US-SM-07 새 전략 화면에 옛 schema YAML을 붙여 넣으면 배너로 올리고 실행 설정을 채워 저장·백테스트한다",
  { tag: ["@story", "@US-SM-07"] },
  async ({ page }) => {
    test.setTimeout(300_000);
    const banner = upgradeBanner(page);
    const summary = page.getByRole("region", { name: "실행 설정 요약" });

    // 새 화면에 1.1 원문을 붙여 넣는다. 저장도 실행도 막히고 배너만 길을 연다.
    await openEditor(page, "/research/strategies/new");
    await replaceSource(page, retiredFixture("quality_momentum.v1_1.yaml"));
    await expectPhase(page, "구조 오류");
    await expect(banner).toContainText(
      "이 문서는 지원이 끝난 schema 버전입니다",
    );
    await expect(save(page)).toBeDisabled();
    await expect(backtest(page)).toBeDisabled();

    // 코드 없는 실패(FastAPI 기본 배열 422)는 한글 문장과 접힌 서버 사유로만 보인다. 영문 진단(`API request
    // failed …`)은 본문에 쓰지 않는다(#270). 실제 서버에서 이 모양을 끌어낼 입력이 없어 한 번만 가로챈다.
    const rejectUpgrade = (route: Route) =>
      route.fulfill({
        status: 422,
        json: {
          detail: [
            { type: "missing", loc: ["body", "source"], msg: "Field required" },
          ],
        },
      });
    await page.route("**/api/v1/strategy-documents/upgrade", rejectUpgrade);
    await upgradeButton(page).click();
    const failure = banner.getByRole("alert");
    await expect(failure).toContainText(
      "업그레이드 요청이 실패했습니다. 원문은 그대로입니다.",
    );
    await expect(failure).not.toContainText("API request failed");
    const reason = failure.getByRole("group");
    await expect(reason).toContainText("서버 사유");
    await expect(reason).toContainText("source: Field required");
    await expect(reason).not.toHaveAttribute("open");
    await page.unroute("**/api/v1/strategy-documents/upgrade", rejectUpgrade);

    const upgraded = await upgradeFromBanner(page);
    expect(upgraded.environment).not.toBeNull();
    expect(upgraded.compiled.schema_version).not.toBeNull();
    await expect(banner).toContainText("현재 버전으로 다시 썼습니다");
    const source = await currentSource(page);
    expect(source).toBe(upgraded.source);
    expect(source).toContain(
      `schema_version: "${upgraded.compiled.schema_version}"`,
    );
    expect(source).not.toContain("\ndata:\n");
    expect(source).not.toContain("\nexecution:\n");
    await expectPhase(page, "검증 통과");

    // 옛 문서의 실행 설정은 문서를 떠나 응답으로 왔다. 누르기 전에는 패널이 비어 있어 실행이 막힌다.
    await expect(summary).toContainText(
      "실행 설정에서 시작일·종료일·유니버스 칸을 채우세요.",
    );
    await expect(backtest(page)).toBeDisabled();
    // 채우지 않고 저장하면 옛 값을 되찾을 길이 없다는 것을 미리 알린다(#267 DEFECT-3).
    await expect(banner).toContainText(
      "채우지 않고 저장하거나 이 화면을 떠나면 이 실행 설정은 다시 볼 수 없습니다.",
    );
    // 실행 취소로 옛 글로 돌아갔다가 다시 실행하면 채우기도 돌아온다(#267 DEFECT-1).
    const fill = banner.getByRole("button", { name: "실행 설정에 채우기" });
    await page.getByRole("button", { name: "실행 취소", exact: true }).click();
    await expect(upgradeButton(page)).toBeVisible();
    await expect(fill).toHaveCount(0);
    await page.getByRole("button", { name: "다시 실행", exact: true }).click();
    expect(await currentSource(page)).toBe(upgraded.source);
    await fill.click();
    await expect(banner).toContainText("옛 문서의 실행 설정을 채웠습니다.");
    await expect(summary).toContainText(upgraded.environment!.universe_id);

    await saveAndWaitForRevision(page, 1);
    await expect(banner).toHaveCount(0);
    const { strategyId } = strategyIdentity(page);
    const saved = requireData(
      (
        await getStrategyDocument({
          client: apiClient,
          path: { strategy_id: strategyId, revision: 1 },
        })
      ).data,
      "get the saved upgraded revision",
    );
    expect(saved.schema_version).toBe(upgraded.compiled.schema_version);
    expect(saved.requires_upgrade).toBe(false);
    expect(saved.source).toBe(upgraded.source);

    // 저장 뒤 revision 화면이 채운 실행 설정을 이어받아 그대로 실행 요청에 싣는다.
    await expect(backtest(page)).toBeEnabled();
    const submittedRun = page.waitForRequest(
      (request) =>
        request.method() === "POST" &&
        new URL(request.url()).pathname === "/api/v1/backtests",
    );
    await backtest(page).click();
    expect((await submittedRun).postDataJSON()).toMatchObject({
      strategy_source: {
        kind: "saved_revision",
        strategy_id: strategyId,
        revision: 1,
        expected_spec_hash: saved.spec_hash,
      },
      environment: requestedEnvironment(upgraded.environment!),
    });
    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
      "completed",
      { timeout: 120_000 },
    );

    // 1.0 원문도 새 화면에서 같은 배너로 현재 버전까지 올라간다(1.0 → 1.1 → 1.2 체인).
    await openEditor(page, "/research/strategies/new");
    await replaceSource(page, retiredFixture("quality_momentum.v1_0.yaml"));
    await expectPhase(page, "구조 오류");
    await expect(banner).toContainText(
      "이 문서는 지원이 끝난 schema 버전입니다",
    );
    const fromV10 = await upgradeFromBanner(page);
    expect(fromV10.environment).toEqual(upgraded.environment);
    await expect(banner).toContainText("현재 버전으로 다시 썼습니다");
    expect(await currentSource(page)).toBe(fromV10.source);
    await expectPhase(page, "검증 통과");
    await expect(save(page)).toBeEnabled();
  },
);

/** fixture 원문의 버전 줄. 버전 문자열은 backend fixture에서만 읽는다. */
const versionLine = (source: string): string => {
  const line = /^schema_version: .*$/mu.exec(source)?.[0];
  if (line === undefined) throw new Error("fixture has no schema_version line");
  return line;
};

test(
  "US-SM-07 업그레이드할 수 없는 옛 문서에는 배너 대신 문제 목록이 고칠 곳을 말한다",
  { tag: ["@story", "@US-SM-07"] },
  async ({ page }) => {
    const banner = upgradeBanner(page);
    const problems = page.getByRole("region", { name: "문제" });
    const v10 = retiredFixture("quality_momentum.v1_0.yaml");
    const v11 = retiredFixture("quality_momentum.v1_1.yaml");

    // 은퇴 버전(1.1)을 적었지만 본문은 그보다 옛(1.0) 문법이다. 업그레이더가 거절하므로 누를 때마다
    // 실패할 배너를 띄우지 않고, 버전 줄과 옛 문법 자리를 고치라고 말한다(#267 DEFECT-2).
    await openEditor(page, "/research/strategies/new");
    await replaceSource(page, v10.replace(versionLine(v10), versionLine(v11)));
    await expectPhase(page, "구조 오류");
    // 문장과 고칠 자리(JSON Pointer)가 문제 목록의 같은 줄에 뜬다.
    const row = (text: string) =>
      problems.getByRole("listitem").filter({ hasText: text });
    await expect(
      row(
        "선언한 schema_version보다 옛 문법이 본문에 섞여 있어 업그레이드할 수 없습니다.",
      ),
    ).toContainText("/schema_version");
    await expect(
      row("1.0 문법입니다. factors 아래에 또 factors 목록을 두던 방식"),
    ).toContainText("/factors");
    await expect(banner).toHaveCount(0);

    // 모르는 버전도 배너 없이 버전 줄을 고치라고 말한다.
    await replaceSource(
      page,
      v11.replace(versionLine(v11), 'schema_version: "9.9"'),
    );
    await expectPhase(page, "구조 오류");
    await expect(
      row(
        "지금 버전도 지원이 끝난 버전도 아닌 schema_version이라 업그레이드할 수 없습니다.",
      ),
    ).toContainText("/schema_version");
    await expect(banner).toHaveCount(0);
    await expect(save(page)).toBeDisabled();
  },
);
