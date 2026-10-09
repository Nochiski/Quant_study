/**
 * 김철수의 스토리 e2e — 원천 필드를 2차 가공한 파생 팩터를 정의하고 기존 팩터와 결합한다(US-CS-02),
 * 그 계산을 실행 계획과 중간값 추적으로 검증하고, 실행 설정이 바뀌거나 서버가 거절한 추적이 옛 결과나 일시
 * 장애처럼 보이지 않는지 본다(US-CS-03).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/cs.md`다. 김철수는 YAML로 팩터
 * 그래프를 직접 적는 사용자라 파생 팩터를 편집기에 입력하는 것부터 시작한다. 분모 필드 id 를 틀린
 * 문서는 저장 전에 compile 이 막는다(lang2 P2-07). 전에는 "검증 통과"로 저장된 뒤 추적에서 멈췄다.
 * 팩터 값과 공개일의 계산은 backend `FactorGraph` 평가가 소유하므로, 여기서는 값을 다시 계산하지
 * 않고 화면에 계산 경로와 결과가 보이는지만 본다.
 */
import { expect, test } from "@playwright/test";

import {
  backtest,
  expectPhase,
  fillRunEnvironment,
  GOLDEN,
  mustReplace,
  openEditor,
  replaceSource,
  RUN_ENVIRONMENT,
  runSettingsInputs,
  saveAndWaitForRevision,
} from "../workbench-helpers";

/**
 * 장부가 대비 시가총액(book-to-market): 자본총계 ÷ 시가총액을 같은 날 종목 사이에서 z-score로 바꾼다.
 * 모멘텀(비중 0.6)과 이 파생 팩터(비중 0.4)를 합성 점수로 결합한다. 결측 처리는 실행 설정의 몫이라
 * (제품 방향, lang2 P2-02) 팩터에 `missing_policy`를 적지 않고 기본값에 맡긴다.
 */
const DERIVED_FACTOR = `  - factor_id: book_to_market
    label: "장부가/시가총액"
    direction: high
    weight: 0.4
    graph:
      nodes:
        - kind: field
          node_id: book
          field_id: financial.book_equity
        - kind: field
          node_id: cap
          field_id: price.market_cap
        - kind: binary
          node_id: btm
          operator: divide
          left_node_id: book
          right_node_id: cap
        - kind: cross_sectional
          node_id: btm_z
          operator: zscore
          input_node_id: btm
      output_node_id: btm_z
`;
const SECURITY_IDS = ["sec-005930-1", "sec-000660-1", "sec-035420-1"];
/** 화면이 숫자를 찍는 모양(`Intl.NumberFormat("en-US")`). 결측은 "—"라 여기에 맞지 않는다. */
const NUMBER = /^-?\d[\d,]*(?:\.\d+)?$/u;
/**
 * 노드 값 상태 배지가 "계산됨"으로 끝난다(backend `ok`, #350). 배지는 색 없이도 읽히도록 숨은 상태
 * 낱말("✓정상")을 앞에 붙이므로 끝만 본다. 결측·오류 상태는 여기에 맞지 않는다.
 */
const STATUS_OK = /계산됨$/u;

test(
  "US-CS-02 두 원천 필드를 나눈 파생 팩터를 모멘텀과 결합해 계획·추적을 확인하고 백테스트한다",
  { tag: ["@story", "@US-CS-02", "@US-CS-03"] },
  async ({ page }) => {
    test.setTimeout(180_000);
    const source = mustReplace(
      mustReplace(GOLDEN, "퀄리티 모멘텀", "US-CS-02 파생 팩터"),
      "portfolio:\n",
      `${DERIVED_FACTOR}portfolio:\n`,
    );
    await openEditor(page, "/research/strategies/new");

    // 분모 필드 id 를 틀리면 연결된 데이터의 필드 계약에 없어 compile 이 막는다(P2-07).
    await replaceSource(
      page,
      mustReplace(
        source,
        "field_id: price.market_cap\n",
        "field_id: price.market_capx\n",
      ),
    );
    await expectPhase(page, "검증 오류");
    const problems = page.getByRole("region", { name: "문제" });
    await expect(problems).toContainText("필드 계약을 찾을 수 없습니다");
    await expect(problems).toContainText("price.market_capx");

    await replaceSource(page, source);
    await expectPhase(page, "검증 통과");
    await saveAndWaitForRevision(page, 1);
    // 추적과 백테스트는 실행 설정의 기간·유니버스 위에서 돈다(P3-02).
    await fillRunEnvironment(page);

    // 중간 결과: 파생 팩터의 마지막 노드를 골라 종목 셋을 추적한다.
    await page
      .getByRole("textbox", { name: "종목 ID", exact: true })
      .fill(SECURITY_IDS.join(", "));
    await page
      .getByRole("combobox", { name: "팩터", exact: true })
      .selectOption("book_to_market");
    await page
      .getByRole("combobox", { name: "노드", exact: true })
      .selectOption("btm_z");
    await page.getByRole("button", { name: "추적 실행" }).click();
    await expect(page.getByLabel("추적 재현 정보")).toBeVisible({
      timeout: 60_000,
    });

    // 연결 추적(기본 탭): 종목마다 합성 점수에 두 팩터가 설정한 비중으로 기여하고, 파생 팩터의 기여도
    // 계산 성공("반영됨")이다. 파생 팩터가 결측이면 여기서 "반영됨"이 아니거나 기여 항목이 빠진다.
    const linked = page.getByRole("list", { name: "연결 추적" });
    for (const securityId of SECURITY_IDS) {
      const pipeline = linked.getByRole("listitem", { name: securityId });
      for (const [factorId, weight] of [
        ["book_to_market", "0.4"],
        ["momentum", "0.6"],
      ] as const) {
        // 한 기여 항목: 팩터 ID · "방향 × 비중" · 정규화 기여도 · 상태.
        const contribution = pipeline
          .locator("span")
          .filter({ has: page.getByText(factorId, { exact: true }) })
          .first();
        await expect(contribution).toHaveText(
          new RegExp(
            `^${factorId}\\s*high × ${weight}\\s*${NUMBER.source.slice(1, -1)}\\D*반영됨$`,
            "u",
          ),
        );
      }
    }

    // 원시 데이터: 파생 팩터가 읽은 두 원천 필드의 값이 종목마다 숫자로 있다.
    await page.getByRole("tab", { name: "원시 데이터" }).click();
    const raw = page.getByRole("tabpanel", { name: "원시 데이터" });
    for (const securityId of SECURITY_IDS)
      for (const fieldId of ["financial.book_equity", "price.market_cap"]) {
        const row = raw
          .getByRole("row")
          .filter({ hasText: securityId })
          .filter({ hasText: fieldId })
          .first();
        await expect(row.getByRole("cell").nth(2)).toHaveText(NUMBER);
      }

    // 선택 노드: 표준화한 파생 팩터(btm_z) 값이 종목마다 숫자이고 상태가 ok다.
    await page.getByRole("tab", { name: "선택 노드" }).click();
    const node = page.getByRole("tabpanel", { name: "선택 노드" });
    for (const securityId of SECURITY_IDS) {
      const cells = node
        .getByRole("row")
        .filter({ hasText: securityId })
        .filter({ hasText: "btm_z" })
        .getByRole("cell");
      // 열: 종목 · 노드 · 연산 · 입력 · 값 · 상태
      await expect(cells.nth(4)).toHaveText(NUMBER);
      await expect(cells.nth(5)).toHaveText(STATUS_OK);
    }

    // 실행 계획: 나눗셈과 횡단면 표준화 단계가 계획에 있다.
    await page.getByRole("tab", { name: "실행 계획" }).click();
    const plan = page.getByRole("tabpanel", { name: "실행 계획" });
    // 계획 행은 "<노드> 소스 열기" 버튼으로 찾는다(노드 ID가 다른 노드 ID의 앞부분이어도 갈린다).
    const planRow = (nodeId: string) =>
      plan.getByRole("row").filter({
        has: page.getByRole("button", {
          name: new RegExp(`^${nodeId} 소스 열기`, "u"),
        }),
      });
    await expect(planRow("btm")).toContainText("binary.divide");
    await expect(
      planRow("btm").getByRole("button", { name: /^book / }),
    ).toBeVisible();
    await expect(
      planRow("btm").getByRole("button", { name: /^cap / }),
    ).toBeVisible();
    await expect(planRow("btm_z")).toContainText("cross_sectional.zscore");

    // 실행 설정만 바꿔도 앞 추적은 새 설정의 결과가 아니다 — 재현 정보가 사라지고 대기 문장으로
    // 돌아간다(#351).
    const settings = runSettingsInputs(page);
    const debuggerRegion = page.getByRole("region", { name: "중간 결과" });
    await settings.toggle.click();
    await settings.fee.fill("20");
    await settings.toggle.click();
    await expect(page.getByLabel("추적 재현 정보")).toBeHidden();
    await expect(
      debuggerRegion.getByText("범위를 선택한 뒤 추적을 실행하세요."),
    ).toBeVisible();

    // 봉인 구간 앞을 측정하는 추적은 서버가 거절한다 — 백테스트 시작과 같은 문장·날짜로 말하고 서버
    // 원문은 접힌 사유에 둔다. 일시 장애("잠시 뒤 다시")로 보이지 않는다(#351).
    await settings.toggle.click();
    await settings.start.fill("2015-01-02");
    await settings.toggle.click();
    await page.getByRole("button", { name: "추적 실행" }).click();
    const failure = debuggerRegion.getByRole("alert", { name: "추적 실패" });
    await expect(failure).toContainText(
      "시작일이 연구 구간 밖입니다. 2016-01-01~2019-12-31은 홀드아웃으로 봉인돼 있고 그 앞도 측정하지 않습니다.",
      { timeout: 60_000 },
    );
    await expect(failure).not.toContainText("잠시 뒤 다시");
    await expect(failure.getByText("서버 사유")).toBeVisible();
    await settings.toggle.click();
    await settings.start.fill(RUN_ENVIRONMENT.start);
    await settings.toggle.click();

    // 결합한 전략이 끝까지 돈다.
    await expect(backtest(page)).toBeEnabled();
    await backtest(page).click();
    await expect(page).toHaveURL(/\/research\/backtests\/[^/?]+$/u);
    await expect(page.getByRole("status", { name: "실행 상태" })).toContainText(
      "completed",
      { timeout: 120_000 },
    );
    await expect(
      page.getByRole("article", { name: "백테스트 결과" }),
    ).toBeVisible({ timeout: 120_000 });
  },
);
