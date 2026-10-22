/**
 * 한상목의 스토리 e2e — 내 PC에서 내 키움 모의 계좌를 연결하고 확인한다(US-SM-18, S06 ②-3).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/sm.md`다. 백엔드는 키움 대신 대본
 * probe(`STRATEGY_WORKBENCH_BROKER_FAKE_PROBE=1`)를 쓴다: 계좌 `5012345678`이면 연결됨, 아니면 계좌 불일치.
 * 키 값은 모두 가짜다.
 */
import { expect, test } from "@playwright/test";

const APP_KEY = "e2e-fake-app-key-0001";
const SECRET = "e2e-fake-secret-0001";
const CONNECTED_ACCOUNT = "5012345678";

test(
  "US-SM-18 설정에서 키움 모의 키를 저장하면 계좌 끝 4자리만 보이고 연결 확인이 계좌 대조 결과를 말한다",
  { tag: ["@story", "@US-SM-18"] },
  async ({ page }) => {
    await page.goto("/settings");
    const section = page.getByRole("region", { name: "키움 모의투자 연결" });
    await expect(section.getByText("저장된 키 없음")).toBeVisible();
    await expect(
      section.getByRole("button", { name: "연결 확인" }),
    ).toBeDisabled();

    const save = async (account: string) => {
      await section.getByLabel("App Key").fill(APP_KEY);
      await section.getByLabel("App Secret").fill(SECRET);
      await section.getByLabel("모의 계좌번호").fill(account);
      await section.getByRole("button", { name: "저장" }).click();
    };

    await save("5012-3456-78");
    await expect(section.getByText("저장됨 · 계좌 끝 5678")).toBeVisible();
    for (const label of ["App Key", "App Secret", "모의 계좌번호"]) {
      await expect(section.getByLabel(label)).toHaveValue("");
    }
    const text = (await page.locator("body").textContent()) ?? "";
    for (const secret of [APP_KEY, SECRET, CONNECTED_ACCOUNT])
      expect(text).not.toContain(secret);

    await section.getByRole("button", { name: "연결 확인" }).click();
    await expect(section.getByText(/^연결됐습니다\./)).toBeVisible();

    await save("5099999999");
    await expect(section.getByText("저장됨 · 계좌 끝 9999")).toBeVisible();
    await section.getByRole("button", { name: "연결 확인" }).click();
    await expect(
      section.getByText(/^이 키에 묶인 계좌가 입력한 계좌번호와 다릅니다\./),
    ).toBeVisible();

    await section.getByRole("button", { name: "삭제", exact: true }).click();
    await section.getByRole("button", { name: "삭제 확인" }).click();
    await expect(section.getByText("저장된 키 없음")).toBeVisible();
  },
);
