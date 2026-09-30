/**
 * schema 1.2 compile 단일 게이트(P2-07)가 새로 내는 진단을 문제 목록과 편집기 마커가 pointer 자리에
 * 보인다(P3-01, Phase 2 감사 #11). 같은 "없는 필드"가 그래프 밖(`strategy.field.missing`)과 그래프 안
 * (`strategy.expression.field_missing`) 두 코드로 오고, 그룹 연산 capability 는 kind `capability`
 * (`strategy.operator.unsupported`)다. 문장은 backend 가 한글로 완성해 보내므로 frontend 는 그대로 쓴다.
 *
 * 응답의 진단 셋은 backend `StrategyAuthoringService.compile` 이 이 문서에 실제로 낸 값이다(필드 계약:
 * `price.close` 숫자, `classification.sector` 숫자 — 그룹 필드가 없다).
 */
import { forEachDiagnostic } from "@codemirror/lint";
import { EditorView } from "@codemirror/view";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import type { SourceDiagnostic } from "../../../shared/api";
import { useCompileDocument } from "../model/use-compile-document";
import { useDiagnosticNavigation } from "../model/use-diagnostic-navigation";
import { useStrategyDocument } from "../model/use-strategy-document";
import { DiagnosticsPanel } from "../ui/diagnostics-panel";
import { SourceEditor } from "../ui/source-editor";

const API = "http://localhost:8000";

const SOURCE = `schema_version: "1.2"
title: "없는 필드"
description: ""
eligibility:
  rules:
    - field_id: price.closex
      operator: gte
      value: 1000
factors:
  - factor_id: momentum
    label: "모멘텀"
    direction: high
    weight: 0.6
    graph:
      nodes:
        - kind: field
          node_id: close
          field_id: price.closee
        - kind: time_series
          node_id: mom_252
          operator: momentum
          input_node_id: close
          window: 252
      output_node_id: mom_252
  - factor_id: sector_neutral
    label: "섹터 중립"
    direction: high
    weight: 0.4
    graph:
      nodes:
        - kind: field
          node_id: close
          field_id: price.close
        - kind: group
          node_id: neutral
          operator: neutralize
          input_node_id: close
          group_field_id: classification.sector
      output_node_id: neutral
portfolio:
  selection_count: 20
  rebalance: monthly
risk:
  max_name_weight: 0.05
parameters: []
`;

const OUTSIDE_GRAPH =
  "연결된 데이터에 없는 필드입니다. 필드 id 를 확인하세요: field_id='price.closex' path='eligibility.rules.0.field_id'";
const INSIDE_GRAPH = "필드 계약을 찾을 수 없습니다: field_id='price.closee'";
const UNSUPPORTED =
  "연결된 데이터가 이 연산에 필요한 필드를 제공하지 않아 실행할 수 없습니다: node_id='neutral' kind='group' operator='neutralize' required='group_series' provided=['numeric_series']";

const DIAGNOSTICS: SourceDiagnostic[] = [
  {
    code: "strategy.field.missing",
    kind: "semantic",
    severity: "error",
    pointer: "/eligibility/rules/0/field_id",
    message: OUTSIDE_GRAPH,
    range: {
      start: { line: 5, column: 16, offset: 91 },
      end: { line: 5, column: 28, offset: 103 },
    },
    node_id: null,
  },
  {
    code: "strategy.expression.field_missing",
    kind: "semantic",
    severity: "error",
    pointer: "/factors/0/graph/nodes/0",
    message: INSIDE_GRAPH,
    range: {
      start: { line: 15, column: 10, offset: 262 },
      end: { line: 18, column: 8, offset: 340 },
    },
    node_id: "close",
  },
  {
    code: "strategy.operator.unsupported",
    kind: "capability",
    severity: "error",
    pointer: "/factors/1/graph/nodes/1",
    message: UNSUPPORTED,
    range: {
      start: { line: 33, column: 10, offset: 697 },
      end: { line: 38, column: 6, offset: 852 },
    },
    node_id: "neutral",
  },
];

const server = setupServer(
  http.post(`${API}/api/v1/strategy-documents/compile`, () =>
    HttpResponse.json({
      format: "yaml",
      source_hash: "s".repeat(64),
      schema_version: "1.2",
      spec: null,
      canonical_json: null,
      spec_hash: null,
      diagnostics: DIAGNOSTICS,
    }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => cleanup());
afterAll(() => server.close());

/** 페이지와 같은 조합: 문제 목록은 편집기 밖에 있고 항해는 훅이 소유한다. */
const Harness = () => {
  const [state, dispatch] = useStrategyDocument({
    kind: "new",
    format: "yaml",
    source: SOURCE,
  });
  useCompileDocument(state, dispatch);
  const problems = useDiagnosticNavigation({
    state,
    view: "yaml",
    sourceView: "yaml",
    form: null,
    tree: null,
    schema: null,
    onSelectPointer: () => undefined,
    onOpenSource: () => undefined,
  });
  return (
    <>
      <SourceEditor
        state={state}
        dispatch={dispatch}
        onEditorReady={problems.onEditorReady}
      />
      <DiagnosticsPanel
        diagnostics={problems.diagnostics}
        stale={problems.stale}
        onSelect={problems.selectDiagnostic}
      />
    </>
  );
};

const mount = async (): Promise<EditorView> => {
  render(<Harness />);
  await screen.findByRole("textbox", { name: "편집기" });
  let view: EditorView | null = null;
  await waitFor(() => {
    const content = document.querySelector(".cm-content");
    view = content ? EditorView.findFromDOM(content as HTMLElement) : null;
    expect(view).not.toBeNull();
  });
  return view as unknown as EditorView;
};

/** 편집기 마커의 문장 → 마커가 덮는 원문. */
const markers = (view: EditorView): Map<string, string> => {
  const found = new Map<string, string>();
  forEachDiagnostic(view.state, (diagnostic, from, to) => {
    found.set(diagnostic.message, view.state.doc.sliceString(from, to));
  });
  return found;
};

describe("schema 1.2 compile 진단의 화면 매핑 (Phase 2 감사 #11)", () => {
  it("없는 필드 두 코드와 capability 진단을 문제 목록과 편집기 마커가 pointer 자리에 보인다", async () => {
    const user = userEvent.setup();
    const view = await mount();
    const panel = await screen.findByRole("region", { name: "문제" });
    await waitFor(() => expect(panel).toHaveTextContent("오류 3 · 경고 0"));

    // 편집기 마커: 세 진단 모두 backend 문장 그대로, pointer 가 짚는 자리에 붙는다.
    await waitFor(() => expect(markers(view).size).toBe(3));
    const marked = markers(view);
    expect(marked.get(OUTSIDE_GRAPH)).toBe("price.closex");
    expect(marked.get(INSIDE_GRAPH)).toMatch(
      /^kind: field\s+node_id: close\s+field_id: price\.closee/,
    );
    expect(marked.get(UNSUPPORTED)).toMatch(
      /^kind: group\s+node_id: neutral\s+operator: neutralize/,
    );

    // 문제 목록: 위치와 pointer 를 보이고, capability 는 "서버" 유형으로 따로 걸러진다.
    expect(
      within(panel).getByRole("button", {
        name: /연결된 데이터에 없는 필드입니다/,
      }),
    ).toHaveTextContent("6:17 /eligibility/rules/0/field_id");
    expect(
      within(panel).getByRole("button", {
        name: /필드 계약을 찾을 수 없습니다/,
      }),
    ).toHaveTextContent("16:11 /factors/0/graph/nodes/0");
    const unsupported = within(panel).getByRole("button", {
      name: /이 연산에 필요한 필드를 제공하지 않아/,
    });
    expect(unsupported).toHaveTextContent("34:11 /factors/1/graph/nodes/1");

    await user.click(unsupported);
    const selection = view.state.selection.main;
    expect(view.state.doc.sliceString(selection.from, selection.to)).toMatch(
      /^kind: group/,
    );

    const filters = within(panel).getByRole("group", {
      name: "문제 유형 필터",
    });
    await user.click(within(filters).getByRole("button", { name: /서버/ }));
    expect(
      within(panel).queryByRole("button", {
        name: /이 연산에 필요한 필드를 제공하지 않아/,
      }),
    ).not.toBeInTheDocument();
    expect(
      within(panel).getByRole("button", {
        name: /연결된 데이터에 없는 필드입니다/,
      }),
    ).toBeInTheDocument();
  });
});
