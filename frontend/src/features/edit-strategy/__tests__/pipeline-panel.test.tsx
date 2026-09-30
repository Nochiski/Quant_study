/**
 * 그래프 1수준 캔버스(WORKFLOW P4-02): 단계 열·요약 띠·문장 안 컨트롤. 입력은 backend runtime schema fixture 와
 * `ideas/*.yaml` 이고, 컨트롤이 내는 연산은 Form 필드 행과 같은 `fieldOperation` 이어야 한다.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DatasetFieldProfile } from "../../../shared/api";
import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import type { DocumentDiagnostic } from "../model/document-state";
import { projectForm, type FormSection } from "../model/form-projection";
import { fieldOperation, type ObjectSection } from "../model/form-transactions";
import type { JsonSchema } from "../model/schema-navigator";
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
    stale: false,
    tree: parse.status === "ok" ? parse.tree : {},
  };
};

const EMPTY = 'schema_version: "1.2"\ntitle: ""\n';
const idea = (name: string) =>
  readBackendFixture(`strategy_documents/ideas/${name}.yaml`);

const transactionsStub = (): SourceTransactions => ({
  apply: vi.fn(() => true),
  run: vi.fn(() => true),
  feedback: { status: "idle" },
  feedbackFor: () => ({ status: "idle" }),
  onEditorReady: vi.fn(),
  enabled: true,
  disabled: null,
  settling: false,
});

const renderPanel = (
  source: string,
  {
    equityFields = null,
    diagnostics = [],
  }: {
    equityFields?: readonly DatasetFieldProfile[] | null;
    diagnostics?: DocumentDiagnostic[];
  } = {},
) => {
  const transactions = transactionsStub();
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

  it("지금 쓰이지 않는 칸은 문서에 없으면 숨고, 적혀 있으면 흐린 채 그 이유를 말한다", () => {
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
});
