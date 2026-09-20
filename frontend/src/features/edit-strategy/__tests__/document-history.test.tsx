import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useCallback } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
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

const source = (): string => screen.getByLabelText("원문").textContent ?? "";
const graph = () =>
  within(screen.getByRole("region", { name: "그래프 편집" }));

describe("문서 되돌리기·다시 실행 (P1-02)", () => {
  it("undoes and redoes one Graph transaction as one step, staying on the Graph view", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await screen.findByRole("textbox", { name: "편집기" });
    await waitFor(() => expect(graph().getByText("편집 가능")).toBeVisible());

    const undoButton = screen.getByRole("button", { name: "되돌리기" });
    const redoButton = screen.getByRole("button", { name: "다시 실행" });
    // 편집 전에는 양쪽 다 비활성이고, 이유가 툴팁이 아닌 문장으로 남는다.
    expect(undoButton).toHaveAttribute("aria-disabled", "true");
    expect(redoButton).toHaveAttribute("aria-disabled", "true");
    expect(
      screen.getByText("되돌리거나 다시 실행할 편집 없음"),
    ).toBeVisible();

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
    expect(screen.getByText("다시 실행할 편집 없음")).toBeVisible();

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
});
