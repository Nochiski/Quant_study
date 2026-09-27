import {
  act,
  cleanup,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import type { CanonicalSnippet } from "../model/canonical-snippets";
import { initialDocumentState } from "../model/document-state";
import type { JsonSchema } from "../model/schema-navigator";
import { useSnippetInsertion } from "../model/use-snippet-insertion";
import { SnippetCatalog } from "../ui/snippet-catalog";

afterEach(cleanup);

const SIGNAL_SCHEMA: JsonSchema = {
  type: "object",
  properties: {
    signal: {
      type: "object",
      properties: { regime_minimum: { type: "number", default: 0.5 } },
    },
  },
};

const SIGNAL_SNIPPET: CanonicalSnippet = {
  id: "section:signal",
  category: "section",
  label: "signal",
  descriptionKey: "strategy.section.signal",
  kind: "section",
  sectionKey: "signal",
  identity: null,
  value: { regime_minimum: 0.5 },
};

const editorHandle: CodeEditorHandle = {
  getText: () => "",
  replaceRange: vi.fn(),
  getSelection: () => ({ from: 0, to: 0 }),
  setSelection: vi.fn(),
  offsetToPosition: vi.fn(() => ({ line: 0, column: 0 })),
  positionToOffset: vi.fn(() => 0),
  scrollTo: vi.fn(),
  focus: vi.fn(),
  loadText: vi.fn(),
  undo: vi.fn(() => false),
  redo: vi.fn(() => false),
  historyDepth: vi.fn(() => ({ undo: 0, redo: 0 })),
  getHistoryState: vi.fn(() => null),
  restoreHistoryState: vi.fn(),
};

describe("snippet insertion coordinator", () => {
  it("does not edit during IME composition", () => {
    const state = { ...initialDocumentState("yaml", ""), composing: true };
    const { result } = renderHook(() =>
      useSnippetInsertion(state, {
        schema: SIGNAL_SCHEMA,
        factors: [],
        status: "ready",
      }),
    );

    act(() => result.current.onEditorReady(editorHandle));
    act(() => result.current.insert(result.current.snippets[0]));

    expect(editorHandle.replaceRange).not.toHaveBeenCalled();
    expect(result.current.feedback).toEqual({
      status: "error",
      label: "signal",
      reason: "composing",
    });
  });

  it("reports an inactive editor before consulting an unmounted projection editor", () => {
    const state = initialDocumentState("yaml", "");
    const { result } = renderHook(() =>
      useSnippetInsertion(
        state,
        { schema: SIGNAL_SCHEMA, factors: [], status: "ready" },
        false,
      ),
    );

    act(() => result.current.insert(result.current.snippets[0]));

    expect(result.current.feedback).toEqual({
      status: "error",
      label: "signal",
      reason: "editor-inactive",
    });
  });

  it("clears feedback when the document epoch or source capability changes", () => {
    const state = initialDocumentState("yaml", "");
    const { result, rerender } = renderHook(
      ({ documentEpoch, status }) =>
        useSnippetInsertion(
          { ...state, documentEpoch },
          { schema: SIGNAL_SCHEMA, factors: [], status },
        ),
      {
        initialProps: {
          documentEpoch: 0,
          status: "ready" as "ready" | "incompatible",
        },
      },
    );
    act(() => result.current.onEditorReady(editorHandle));
    act(() => result.current.insert(result.current.snippets[0]));
    expect(result.current.feedback.status).toBe("inserted");

    rerender({ documentEpoch: 1, status: "ready" });
    expect(result.current.feedback).toEqual({ status: "idle" });
    act(() => result.current.insert(result.current.snippets[0]));
    expect(result.current.feedback.status).toBe("inserted");
    rerender({ documentEpoch: 1, status: "incompatible" });
    expect(result.current.feedback).toEqual({ status: "idle" });
    rerender({ documentEpoch: 1, status: "ready" });
    expect(result.current.feedback).toEqual({ status: "idle" });
  });
});

describe("SnippetCatalog", () => {
  it("renders the section and example groups and invokes the labelled keyboard button", async () => {
    const user = userEvent.setup();
    const onInsert = vi.fn();
    render(
      <SnippetCatalog
        snippets={[SIGNAL_SNIPPET]}
        sourceStatus="ready"
        feedback={{ status: "idle" }}
        onInsert={onInsert}
      />,
    );

    // schema 1.2: 섹션은 스키마에서, 팩터 preset 은 튜토리얼용 "예시"로 강등(P3-01).
    expect(
      screen.getAllByRole("heading").map((heading) => heading.textContent),
    ).toEqual(["섹션", "예시 팩터"]);
    expect(screen.getByText(/튜토리얼용 예시입니다/)).toBeInTheDocument();
    // 섹션은 이름을 먼저, 스키마 키를 보조로 보인다.
    await user.click(
      screen.getByRole("button", { name: "신호 결합 · 현재 커서에 삽입" }),
    );
    expect(onInsert).toHaveBeenCalledWith(SIGNAL_SNIPPET);
  });

  it.each([
    ["loading", { status: "idle" }, "status", "불러오는 중"],
    ["unavailable", { status: "idle" }, "alert", "메타데이터를 불러올 수 없어"],
    ["incompatible", { status: "idle" }, "alert", "버전이 일치하지 않아"],
    [
      "ready",
      { status: "error", label: "signal", reason: "duplicate" },
      "alert",
      "같은 항목이 이미 존재합니다",
    ],
  ] as const)(
    "announces %s catalog state",
    (sourceStatus, feedback, role, text) => {
      render(
        <SnippetCatalog
          snippets={sourceStatus === "ready" ? [SIGNAL_SNIPPET] : []}
          sourceStatus={sourceStatus}
          feedback={feedback}
          onInsert={vi.fn()}
        />,
      );
      expect(screen.getByRole(role)).toHaveTextContent(text);
    },
  );
});
