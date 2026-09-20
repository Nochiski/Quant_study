import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useCallback } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import type { DocumentSource } from "../model/document-source";
import type { JsonSchema } from "../model/schema-navigator";
import { useDocumentHistory } from "../model/use-document-history";
import { useFormProjection } from "../model/use-form-projection";
import { useSourceTransactions } from "../model/use-source-transactions";
import { useStrategyDocument } from "../model/use-strategy-document";
import { DocumentHistoryActions } from "../ui/document-history";
import { FactorGraphPanel } from "../ui/factor-graph-panel";
import { SourceEditor } from "../ui/source-editor";

afterEach(cleanup);

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;
const VERBOSE = readBackendFixture("strategy_documents/quality_momentum.yaml");

/**
 * Graph 탭에 머문 채 되돌리기·다시 실행이 도는 경로(WORKFLOW P1-02). 편집기는 hidden 탭에서처럼 포커스를
 * 갖지 않고 버튼만 누른다. 트랜잭션 한 번 = 되돌리기 한 단계라는 기존 불변식(`replaceRange` history 격리)의
 * 회귀 테스트이기도 하다.
 */
const Harness = () => {
  const [doc, dispatch] = useStrategyDocument({
    kind: "new",
    format: "yaml",
    source: VERBOSE,
  });
  const transactions = useSourceTransactions(doc, true);
  const history = useDocumentHistory(doc);
  const form = useFormProjection(doc, SCHEMA);
  const onTransactionsEditorReady = transactions.onEditorReady;
  const onHistoryEditorReady = history.onEditorReady;
  const onEditorReady = useCallback(
    (editor: CodeEditorHandle | null): void => {
      onTransactionsEditorReady(editor);
      onHistoryEditorReady(editor);
    },
    [onHistoryEditorReady, onTransactionsEditorReady],
  );
  return (
    <>
      <DocumentHistoryActions history={history} />
      <FactorGraphPanel
        state={{ status: "blocked", reason: "invalid" }}
        diagnostics={[]}
        selectedPointer="/factors/0/graph"
        onSelectPointer={vi.fn()}
        onOpenSource={vi.fn()}
        editing={{
          tree: form.tree,
          schema: SCHEMA,
          transactions,
          catalogs: { equityFields: null, factors: null },
        }}
      />
      {/* 편집기는 늘 마운트되어 있다 — Graph 탭에서는 hidden이라 포커스만 없다. */}
      <SourceEditor state={doc} dispatch={dispatch} onEditorReady={onEditorReady} />
      {/* YAML 탭이 보여 주는 원문. 편집기 텍스트와 같은 값이다. */}
      <pre aria-label="원문">{doc.source}</pre>
    </>
  );
};

const A = 'schema_version: "1.1"\ntitle: 리비전 A\n';
const B = 'schema_version: "1.1"\ntitle: 리비전 B\n';

const revision = (index: number, text: string): DocumentSource => ({
  kind: "revision",
  document: {
    strategy_id: "s1",
    revision: index,
    schema_version: "1.1",
    format: "yaml",
    source: text,
    source_hash: String(index).repeat(64),
    spec: { title: `리비전 ${index}` } as never,
    spec_hash: String(index).repeat(64),
    origin: "document",
    generated: false,
    requires_upgrade: false,
    created_at: "2026-09-04T09:30:00+00:00",
  },
});

/** 편집기와 되돌리기 버튼만 있는 최소 배선. 문서를 갈아 끼우며 이력 초기화를 본다. */
const RevisionHarness = ({
  source: documentSource,
}: {
  source: DocumentSource;
}) => {
  const [doc, dispatch] = useStrategyDocument(documentSource);
  const history = useDocumentHistory(doc);
  return (
    <>
      <DocumentHistoryActions history={history} />
      <SourceEditor
        state={doc}
        dispatch={dispatch}
        onEditorReady={history.onEditorReady}
      />
      <pre aria-label="원문">{doc.source}</pre>
    </>
  );
};

const source = (): string => screen.getByLabelText("원문").textContent ?? "";
const graph = () =>
  within(screen.getByRole("region", { name: "그래프 편집" }));

describe("문서 되돌리기·다시 실행 (P1-02)", () => {
  it("undoes and redoes one Graph transaction as one step, staying on the Graph view", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await screen.findByRole("textbox", { name: "편집기" });
    await waitFor(() => expect(graph().getByText("편집 가능")).toBeVisible());

    const undoButton = screen.getByRole("button", { name: /실행 취소/ });
    const redoButton = screen.getByRole("button", { name: /다시 실행/ });
    // 편집 전에는 양쪽 다 비활성이고, 이유는 버튼에 연결된 설명으로 읽힌다(툴팁 문구가 아니다).
    expect(undoButton).toHaveAttribute("aria-disabled", "true");
    expect(redoButton).toHaveAttribute("aria-disabled", "true");
    expect(undoButton).toHaveAccessibleDescription("되돌릴 편집이 없습니다");
    expect(redoButton).toHaveAccessibleDescription("다시 실행할 편집이 없습니다");

    const before = source();
    expect(before).toBe(VERBOSE);

    await user.selectOptions(
      graph().getByRole("combobox", { name: "노드 종류" }),
      "field",
    );
    await user.click(graph().getByRole("button", { name: "노드 추가" }));
    await waitFor(() =>
      expect(source()).toContain("\n          node_id: field\n"),
    );
    // 깊이 표시는 편집 직후 갱신된다(폴링 없이 sourceVersion 변화로).
    await waitFor(() => expect(undoButton).not.toHaveAttribute("aria-disabled"));
    expect(redoButton).toHaveAccessibleDescription("다시 실행할 편집이 없습니다");

    // 편집기는 포커스를 갖지 않는다 — Graph 탭에서 hidden인 편집기와 같은 상태다.
    expect(document.activeElement).not.toBe(
      document.querySelector(".cm-content"),
    );
    await user.click(undoButton);
    await waitFor(() => expect(source()).toBe(before));
    expect(redoButton).not.toHaveAttribute("aria-disabled");

    await user.click(redoButton);
    await waitFor(() =>
      expect(source()).toContain("\n          node_id: field\n"),
    );
  });

  it("drops the history when another revision is loaded", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<RevisionHarness source={revision(1, A)} />);
    const textbox = await screen.findByRole("textbox", { name: "편집기" });
    const undoButton = screen.getByRole("button", { name: /실행 취소/ });

    // 리비전 A를 편집하면 되돌릴 단계가 생긴다.
    await user.click(textbox);
    await user.keyboard("x");
    await waitFor(() => expect(source()).not.toBe(A));
    await waitFor(() => expect(undoButton).not.toHaveAttribute("aria-disabled"));

    // 리비전 B로 갈아 끼우면 A의 편집 단계까지 사라진다 — 되돌리기로 앞 문서에 닿을 수 없다.
    rerender(<RevisionHarness source={revision(2, B)} />);
    await waitFor(() => expect(source()).toBe(B));
    await waitFor(() =>
      expect(undoButton).toHaveAttribute("aria-disabled", "true"),
    );
    await user.click(undoButton);
    expect(source()).toBe(B);
  });
});
