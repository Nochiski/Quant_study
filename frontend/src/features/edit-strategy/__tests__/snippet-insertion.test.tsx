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
      properties: { method: { type: "string", default: "weighted_sum" } },
    },
  },
};

const SIGNAL_SNIPPET: CanonicalSnippet = {
  id: "section:signal",
  category: "signal",
  label: "signal",
  kind: "section",
  sectionKey: "signal",
  collectionKey: null,
  identity: null,
  value: { method: "weighted_sum" },
};

const editorHandle: CodeEditorHandle = {
  getText: () => "",
  setText: vi.fn(),
  replaceRange: vi.fn(),
  getSelection: () => ({ from: 0, to: 0 }),
  setSelection: vi.fn(),
  offsetToPosition: vi.fn(() => ({ line: 0, column: 0 })),
  positionToOffset: vi.fn(() => 0),
  scrollTo: vi.fn(),
  focus: vi.fn(),
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
});

describe("SnippetCatalog", () => {
  it("renders all five areas and invokes the labelled keyboard button", async () => {
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

    for (const heading of ["데이터", "팩터", "신호", "리스크", "실행"]) {
      expect(
        screen.getByRole("heading", { name: heading }),
      ).toBeInTheDocument();
    }
    await user.click(
      screen.getByRole("button", { name: "signal · 현재 커서에 삽입" }),
    );
    expect(onInsert).toHaveBeenCalledWith(SIGNAL_SNIPPET);
  });

  it.each([
    ["loading", { status: "idle" }, "status", "불러오는 중"],
    ["unavailable", { status: "idle" }, "alert", "서버 스키마가 없어"],
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
