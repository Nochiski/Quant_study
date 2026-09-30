/**
 * 그래프 1수준 캔버스(WORKFLOW P4-02): 단계 열·요약 띠·문장 안 컨트롤. 입력은 backend runtime schema fixture 와
 * `ideas/*.yaml` 이고, 캔버스가 내는 연산은 Form 과 같은 것(`fieldOperation`·`resetOperation`·`unsetOperation`·
 * `listAddition`·`removeItemOperation`)이어야 한다.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DatasetFieldProfile } from "../../../shared/api";
import { t } from "../../../shared/config";
import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import type { DocumentDiagnostic } from "../model/document-state";
import {
  projectForm,
  type FormListSection,
  type FormSection,
} from "../model/form-projection";
import {
  fieldOperation,
  listAddition,
  removeItemOperation,
  resetOperation,
  unsetOperation,
  type ObjectSection,
} from "../model/form-transactions";
import type { JsonSchema } from "../model/schema-navigator";
import { NO_FOCUS } from "../model/use-field-editing";
import type { FormProjectionState } from "../model/use-form-projection";
import type { SourceTransactions } from "../model/use-source-transactions";
import { PipelinePanel } from "../ui/pipeline-panel";

afterEach(cleanup);

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;

const formOf = (
  source: string,
  diagnostics: DocumentDiagnostic[] = [],
): FormProjectionState => {
  const parse = parseSource(source, "yaml");
  return {
    projection: projectForm(SCHEMA, parse, diagnostics),
    parsing: false,
    stale: false,
    tree: parse.status === "ok" ? parse.tree : {},
  };
};

const EMPTY = 'schema_version: "1.2"\ntitle: ""\n';
const idea = (name: string) =>
  readBackendFixture(`strategy_documents/ideas/${name}.yaml`);

const transactionsStub = (settling = false): SourceTransactions => ({
  apply: vi.fn(() => true),
  run: vi.fn(() => true),
  feedback: { status: "idle" },
  feedbackFor: () => ({ status: "idle" }),
  onEditorReady: vi.fn(),
  enabled: true,
  disabled: null,
  settling,
});

const renderPanel = (
  source: string,
  {
    equityFields = null,
    diagnostics = [],
    settling = false,
  }: {
    equityFields?: readonly DatasetFieldProfile[] | null;
    diagnostics?: DocumentDiagnostic[];
    settling?: boolean;
  } = {},
) => {
  const transactions = transactionsStub(settling);
  const onOpenGraph = vi.fn();
  const view = render(
    <PipelinePanel
      form={formOf(source, diagnostics)}
      schema={SCHEMA}
      transactions={transactions}
      catalogs={{ equityFields }}
      onOpenGraph={onOpenGraph}
    />,
  );
  return { ...view, transactions, onOpenGraph };
};

const diagnostic = (pointer: string, message: string): DocumentDiagnostic => ({
  code: "strategy.test",
  kind: "semantic",
  severity: "error",
  pointer,
  message,
  range: null,
});

const objectSection = (sections: FormSection[], key: string): ObjectSection => {
  const found = sections.find((section) => section.key === key);
  if (found === undefined || found.kind !== "object") throw new Error(key);
  return found;
};

const rulesOf = (source: string): FormListSection =>
  objectSection(
    projectForm(SCHEMA, parseSource(source, "yaml"), []).sections,
    "eligibility",
  ).lists.find((list) => list.key === "rules")!;

describe("PipelinePanel", () => {
  it("단계 이름·영문 소제목·한 줄 설명 아래 카드를 두고, 맨 위에 한 문장 요약을 보인다", () => {
    renderPanel(idea("momentum_12_1"));

    for (const name of [
      "1 유니버스 Universe",
      "2 알파 팩터 Alpha",
      "3 포트폴리오 구성 Portfolio",
      "4 리스크 제약 Risk",
    ])
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    expect(
      screen.getByText(/높은 12-1 모멘텀 순으로, .* 골라 보유한다\./),
    ).toBeInTheDocument();
  });

  it("카드 문장 안의 컨트롤로 값을 바꾸면 Form 행과 같은 연산이 나간다", async () => {
    const user = userEvent.setup();
    const { transactions } = renderPanel(EMPTY);
    const card = screen.getByRole("group", { name: "선택 방식" });
    expect(card).toHaveTextContent(/합산 점수 .*종목을 고른다/);
    const count = within(card).getByRole("spinbutton", {
      name: "롱 포트폴리오에 선택할 종목 수",
    });

    await user.clear(count);
    await user.type(count, "30");
    await user.tab();

    const form = projectForm(SCHEMA, parseSource(EMPTY, "yaml"), []);
    const portfolio = objectSection(form.sections, "portfolio");
    const field = portfolio.fields.find(
      (item) => item.key === "selection_count",
    )!;
    expect(transactions.apply).toHaveBeenCalledWith(
      fieldOperation(portfolio, field, 30),
      "롱 포트폴리오에 선택할 종목 수",
      "pipeline",
      { focusEditor: false },
    );
  });

  it("틀 밖의 지금 쓰이지 않는 칸은 문서에 없으면 숨고, 적혀 있으면 흐리게 보이며 이유를 설명으로 단다", () => {
    renderPanel(EMPTY);
    const weighting = screen.getByRole("group", { name: "비중 산정" });
    expect(
      within(weighting).queryByRole("textbox", { name: "위험 필드" }),
    ).toBeNull();
    cleanup();

    renderPanel(`${EMPTY}risk:\n  risk_field_id: price.trading_value\n`);
    const written = within(
      screen.getByRole("group", { name: "비중 산정" }),
    ).getByRole("textbox", { name: "위험 필드" });
    expect(written).toHaveAccessibleDescription(
      /현재 모드에서는 읽히지 않는 필드입니다/,
    );
  });

  it("규칙은 문장 카드, 팩터는 이름과 그래프 열기로 보이고 YAML 식별자는 보이지 않는다", async () => {
    const user = userEvent.setup();
    const { container, onOpenGraph } = renderPanel(idea("low_pbr_high_roe"));

    const rules = screen.getByRole("group", { name: "거르기 규칙 목록" });
    const rule = within(rules).getByRole("group", { name: "1번째 항목" });
    expect(rule).toHaveTextContent("종목만 — 기준값");
    expect(
      within(rule).getByRole("combobox", { name: "비교 방식" }),
    ).toHaveValue("gt");

    const factors = screen.getByRole("group", { name: "알파 팩터" });
    expect(within(factors).getByText("PBR")).toBeInTheDocument();
    await user.click(
      within(factors).getByRole("button", { name: "ROE · Graph에서 열기" }),
    );
    expect(onOpenGraph).toHaveBeenCalledWith("/factors/1/graph");

    // P4-04 의 "파이프라인 수준 DOM 에 식별자 0개" e2e 단언의 선행 가드.
    expect(container.textContent).not.toMatch(/_id|_node|kind:/);
  });

  it("카탈로그·참조 선택지는 식별자 대신 요약과 같은 이름을 보인다", () => {
    renderPanel(idea("low_pbr_high_roe"), {
      equityFields: [
        { field_id: "financial.book_equity", label: "자본총계" },
        { field_id: "price.trading_value", label: "거래대금" },
      ] as DatasetFieldProfile[],
    });
    const rule = within(
      screen.getByRole("group", { name: "거르기 규칙 목록" }),
    ).getByRole("group", { name: "1번째 항목" });
    const field = within(rule).getByRole("combobox", { name: "데이터 필드" });
    expect(field).toHaveDisplayValue("자본총계");
    expect(field).not.toHaveTextContent(/financial\.|price\./);
    cleanup();

    renderPanel(idea("inverse_volatility"));
    const source = within(
      screen.getByRole("group", { name: "비중 산정" }),
    ).getByRole("combobox", { name: "위험 팩터" });
    expect(source).toHaveDisplayValue("60일 변동성");
    expect(source).toHaveTextContent("12-1 모멘텀");
    expect(source).not.toHaveTextContent(/momentum_12_1|volatility_60/);
  });

  it("팩터 이름 카드는 자기 필드의 진단만 보이고 그래프 안 진단은 아래 고급 편집기에 맡긴다", () => {
    renderPanel(idea("low_pbr_high_roe"), {
      diagnostics: [
        diagnostic("/factors/0/graph/nodes/0", "노드 문장"),
        diagnostic("/factors/0/weight", "가중치 문장"),
      ],
    });
    const factor = within(
      screen.getByRole("group", { name: "알파 팩터" }),
    ).getByRole("group", { name: "PBR" });
    expect(factor).toHaveTextContent("가중치 문장");
    expect(factor).not.toHaveTextContent("노드 문장");
  });

  it("비중 카드의 위험 필드는 카드 앵커(portfolio)가 아니라 자기 섹션(risk)으로 확정한다", async () => {
    // 앵커 섹션으로 확정하면 `risk_field_id` 가 `portfolio:` 아래 적혀 구조 오류가 난다(#392 리뷰 P3-1 B).
    const user = userEvent.setup();
    const source = `${EMPTY}portfolio:\n  weighting: risk\n`;
    const { transactions } = renderPanel(source);
    await user.type(
      within(screen.getByRole("group", { name: "비중 산정" })).getByRole(
        "textbox",
        { name: "위험 필드" },
      ),
      "price.trading_value{Enter}",
    );

    const risk = objectSection(
      projectForm(SCHEMA, parseSource(source, "yaml"), []).sections,
      "risk",
    );
    const field = risk.fields.find((item) => item.key === "risk_field_id")!;
    expect(transactions.apply).toHaveBeenCalledWith(
      fieldOperation(risk, field, "price.trading_value"),
      "위험 필드",
      "pipeline",
      NO_FOCUS,
    );
  });

  it("카드의 기본값으로·설정 안 함은 Form 행과 같은 연산을 필드 이름으로 낸다", async () => {
    // nullable 칸은 빈 입력이 무효라 캔버스에서 값을 지우는 길은 "설정 안 함"뿐이다(#392 리뷰 P3-1 C).
    const user = userEvent.setup();
    const source = `${EMPTY}signal:\n  score_threshold: 0.5\n`;
    const { transactions } = renderPanel(source);
    const card = screen.getByRole("group", { name: "점수 하한" });
    await user.click(
      within(card).getByRole("button", { name: "점수 하한 · 기본값으로" }),
    );
    await user.click(
      within(card).getByRole("button", { name: "점수 하한 · 설정 안 함" }),
    );

    const signal = objectSection(
      projectForm(SCHEMA, parseSource(source, "yaml"), []).sections,
      "signal",
    );
    const field = signal.fields.find((item) => item.key === "score_threshold")!;
    expect(transactions.apply).toHaveBeenNthCalledWith(
      1,
      resetOperation(field),
      "점수 하한",
      "pipeline",
      NO_FOCUS,
    );
    expect(transactions.apply).toHaveBeenNthCalledWith(
      2,
      unsetOperation(signal, field),
      "점수 하한",
      "pipeline",
      NO_FOCUS,
    );
  });

  it("정규화 단위 경고는 정규화 카드 안에 본문으로 보인다", () => {
    // backend 는 단위 경고를 `/signal/normalization` 에 낸다 — 행 진단으로 카드에 붙는다(acceptance).
    renderPanel(idea("momentum_12_1"), {
      diagnostics: [
        diagnostic(
          "/signal/normalization",
          "단위가 다른 팩터를 원값 그대로 더합니다",
        ),
      ],
    });
    expect(
      screen.getByRole("group", { name: "점수 정규화" }),
    ).toHaveTextContent("단위가 다른 팩터를 원값 그대로 더합니다");
  });

  it("규칙 목록은 Form 목록과 같은 연산으로 항목을 더하고 지운다", async () => {
    const user = userEvent.setup();
    const source = idea("low_pbr_high_roe");
    const { transactions } = renderPanel(source);
    const rules = screen.getByRole("group", { name: "거르기 규칙 목록" });

    await user.click(
      within(rules).getByRole("button", {
        name: "거르기 규칙 목록 · 항목 추가",
      }),
    );
    await user.click(
      within(rules).getByRole("button", { name: "1번째 항목 · 삭제" }),
    );

    const list = rulesOf(source);
    expect(transactions.apply).toHaveBeenNthCalledWith(
      1,
      listAddition(SCHEMA, list, null, false).operation,
      "거르기 규칙 목록",
      "pipeline",
      NO_FOCUS,
    );
    expect(transactions.apply).toHaveBeenNthCalledWith(
      2,
      removeItemOperation(list.items[0]!),
      "1번째 항목",
      "pipeline",
      NO_FOCUS,
    );
  });

  it("직전 편집이 반영되는 중에는 규칙 추가·삭제를 잠그고 추가 버튼이 이유를 말한다", () => {
    // Form 목록의 P1-04 잠금과 같다. P4-04 가 Form 목록을 걷어도 캔버스에 남는다(#395 리뷰 P3-3).
    renderPanel(idea("low_pbr_high_roe"), { settling: true });
    const rules = screen.getByRole("group", { name: "거르기 규칙 목록" });
    const add = within(rules).getByRole("button", {
      name: "거르기 규칙 목록 · 항목 추가",
    });
    expect(add).toBeDisabled();
    expect(add).toHaveAccessibleDescription(t("form.list.addSettling"));
    expect(
      within(rules).getByRole("button", { name: "1번째 항목 · 삭제" }),
    ).toBeDisabled();
  });

  it("5 실행 안내는 실행 설정 자리와 캔버스 밖에 남는 것을 스키마대로 말한다", () => {
    renderPanel(EMPTY);
    const guide = screen.getByRole("group", { name: "5 실행 Execution" });
    expect(guide).toHaveTextContent("화면 위 실행 설정에서 고릅니다");
    // 단계 없는 섹션(`x-stage` 없음)의 이름이다. 문서 버전 스탬프(const)는 빠진다.
    expect(guide).toHaveTextContent(
      "전략 이름·전략 설명·탐색 파라미터: YAML·Form 탭에서 고칩니다.",
    );
    expect(guide).toHaveTextContent(
      "팩터 계산식: 아래 고급 편집기에서 고칩니다.",
    );
  });
});
