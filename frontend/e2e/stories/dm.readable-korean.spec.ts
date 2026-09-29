/**
 * 정동민의 스토리 e2e — 화면의 말과 오류 문장을 쉬운 한글로 읽는다(US-DM-06).
 *
 * 스토리 문구와 수용 기준의 정본은 `docs/product/user-stories/stories/dm.md`다. 이름·설명 키와 연산자
 * 목록은 backend가, 문장은 frontend i18n(화면 어휘)과 backend(진단 문장)가 소유한다. 여기서는 영어
 * 식별자를 모르는 사람이 화면에서 무엇을 읽는지만 본다. 레이아웃 계약(노드 카드 본문 위치 등)은
 * `workbench.workflow.spec.ts`의 P1-04 테스트가 소유하므로 다시 재지 않는다.
 */
import { expect, test } from "@playwright/test";

import type {
  BacktestRunSpec,
  BacktestRunState,
  PageBacktestRunSummary,
} from "../../src/shared/api/generated";
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
  "US-DM-06 필드 이름을 틀리거나 1.0 문법을 쓰면 문제 목록이 한글로 고칠 방법을 말한다",
  { tag: ["@story", "@US-DM-06"] },
  async ({ page }) => {
    const valid = mustReplace(GOLDEN, "퀄리티 모멘텀", "US-DM-06 오류 문장");
    await openEditor(page, "/research/strategies/new");
    await replaceSource(page, valid);
    await expectPhase(page, "검증 통과");
    // 저장된 리비전 화면에서 고쳐 쓴다(업그레이드 배너가 사는 화면이라 배너가 없는지도 본다).
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

    // 1.0에서만 쓰던 키를 적으면: 문장이 그 사실과 할 일(지우기)을 말한다. 문서가 현재 버전을
    // 선언했으므로 업그레이드 대상이 아니고 배너도 없다(lang2 Phase 2 감사 NB-1). schema
    // 1.2(P2-03)에는 `execution` 절이 없어 1.0 의 `signal.method` 를 쓴다.
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
    await expect(problems).toContainText("지우세요");
    await expect(problems).not.toContainText("업그레이드");
    await expect(
      page.getByRole("region", { name: "이전 schema 문서" }),
    ).toHaveCount(0);
  },
);

/**
 * 연구 구간 규칙이 생기기 전 기간으로 돌다 자본 잠식으로 멈춘 옛 실행. 이력·결과 화면·재실행은 브라우저에서
 * 대신 응답한다 — 실패한 실행을 실제로 만들면 e2e 이력 DB에 남아 다른 스토리의 이력 단언을 흔든다. 문장은
 * frontend 번역(run 실패 코드·시작 거절 코드)이 소유한다.
 */
const WIPED_OUT: BacktestRunState = {
  run_id: "run-dm06-wiped-out",
  status: "failed",
  progress: 0.6,
  stage: "engine",
  message: "Run failed",
  error:
    "EquityWipedOutError: session-end equity fell to or below zero — session=2018-03-02 equity=-1204.5",
  error_code: "backtest.run.equity_wiped_out",
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:05Z",
};

const WIPED_OUT_REQUEST: BacktestRunSpec = {
  environment: {
    start: "2018-01-02",
    end: "2019-12-30",
    universe_id: "krx.common-stock",
  },
  strategy_source: {
    kind: "saved_revision",
    strategy_id: "strategy-dm06",
    revision: 3,
    expected_spec_hash: "d".repeat(64),
  },
};

test(
  "US-DM-06 실패한 옛 실행은 백테스트 이력과 결과 화면에서 같은 한글 문장으로 보이고 재실행 거절은 고칠 곳을 말한다",
  { tag: ["@story", "@US-DM-06"] },
  async ({ page }) => {
    const history: PageBacktestRunSummary = {
      items: [
        {
          run: WIPED_OUT,
          strategy_provenance: {
            kind: "saved_revision",
            strategy_id: "strategy-dm06",
            revision: 3,
            schema_version: "1.2",
            spec_hash: "d".repeat(64),
            source_hash: "e".repeat(64),
          },
        },
      ],
      total: 1,
      offset: 0,
      limit: 25,
    };
    await page.route(
      (url) => url.pathname === "/api/v1/backtests",
      (route) =>
        route.request().method() === "GET"
          ? route.fulfill({ json: history })
          : route.fulfill({
              status: 422,
              json: {
                detail: {
                  code: "backtest.run.research_window_violation",
                  message:
                    "측정 시작일이 연구 구간 밖이라 실행할 수 없다 — got=start=2018-01-02",
                  sealed_start: "2016-01-01",
                  sealed_end: "2019-12-31",
                  research_start: "2020-01-02",
                },
              },
            }),
    );
    await page.route(`**/api/v1/backtests/${WIPED_OUT.run_id}`, (route) =>
      route.fulfill({ json: WIPED_OUT }),
    );
    await page.route(
      `**/api/v1/backtests/${WIPED_OUT.run_id}/request`,
      (route) => route.fulfill({ json: WIPED_OUT_REQUEST }),
    );
    const sentence =
      "세션 종료 자산이 0 이하가 되어 실행이 멈췄습니다(자본 잠식).";

    // 이력 줄은 서버 원문 대신 결과 화면과 같은 한글 문장을 보이고, 원문은 접힌 "서버 사유"에만 둔다.
    const response = await page.goto("/research/backtests");
    expect(response?.ok()).toBe(true);
    const row = page.getByRole("row").filter({ hasText: WIPED_OUT.run_id });
    await expect(row.getByText(`실행 오류: ${sentence}`)).toBeVisible();
    await expect(row.getByRole("group")).toContainText("EquityWipedOutError");
    await expect(row).not.toContainText("EquityWipedOutError", {
      useInnerText: true,
    });
    // 지난 실행이라 줄마다 경고(alert)로 읽히지 않는다.
    await expect(row.getByRole("alert")).toHaveCount(0);

    // 결과 화면도 같은 문장이다.
    await row.getByRole("link", { name: "실행 열기" }).click();
    await expect(page).toHaveURL(`/research/backtests/${WIPED_OUT.run_id}`);
    await expect(page.getByRole("alert", { name: "실행 오류" })).toContainText(
      sentence,
    );

    // 결과 화면에는 실행 설정 패널이 없다 — 재실행 거절이 어디서 무엇을 고칠지 말한다.
    await page.getByRole("button", { name: "동일 설정 재실행" }).click();
    const rejection = page.getByRole("alert", {
      name: "동일 설정으로 다시 실행하지 못했습니다",
    });
    await expect(rejection).toContainText(
      "전략 편집기의 실행 설정에서 시작일을 2020-01-02 이후로 옮긴 뒤 다시 시작하세요.",
    );
    await expect(rejection.getByRole("group")).toContainText(
      "got=start=2018-01-02",
    );
    await expect(rejection).not.toContainText("got=start", {
      useInnerText: true,
    });
  },
);
