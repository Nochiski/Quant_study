import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import {
  initialDocumentState,
  type DocumentState,
} from "../model/document-state";
import { useSourceTransactions } from "../model/use-source-transactions";

const SOURCE = 'schema_version: "1.1"\nrisk:\n  max_name_weight: 0.05\n';

/** 문서 하나를 가진 편집기 handle 대역. replaceRange는 텍스트를 실제로 바꾼다. */
const editorOf = (initial: string) => {
  let text = initial;
  const handle: CodeEditorHandle = {
    getText: () => text,
    setText: vi.fn((next: string) => {
      text = next;
    }),
    replaceRange: vi.fn((from: number, to: number, insert: string) => {
      text = `${text.slice(0, from)}${insert}${text.slice(to)}`;
    }),
    getSelection: () => ({ from: 0, to: 0 }),
    setSelection: vi.fn(),
    offsetToPosition: vi.fn(() => ({ line: 0, column: 0 })),
    positionToOffset: vi.fn(() => 0),
    scrollTo: vi.fn(),
    focus: vi.fn(),
    getHistoryState: vi.fn(() => null),
    restoreHistoryState: vi.fn(),
  };
  return { handle, text: () => text };
};

const parsedState = (source: string): DocumentState => {
  const base = initialDocumentState("yaml", source);
  return {
    ...base,
    parse: parseSource(source, "yaml"),
    parsedVersion: base.sourceVersion,
  };
};

describe("useSourceTransactions", () => {
  it("applies one operation to the editor's current text through replaceRange", () => {
    const editor = editorOf(SOURCE);
    const { result } = renderHook(() =>
      useSourceTransactions(parsedState(SOURCE)),
    );
    expect(result.current.enabled).toBe(false);
    act(() => result.current.onEditorReady(editor.handle));
    expect(result.current.enabled).toBe(true);

    act(() =>
      result.current.apply(
        {
          kind: "replace-scalar",
          pointer: "/risk/max_name_weight",
          value: 0.1,
        },
        "max_name_weight",
      ),
    );

    expect(editor.handle.replaceRange).toHaveBeenCalledTimes(1);
    expect(editor.text()).toBe(
      'schema_version: "1.1"\nrisk:\n  max_name_weight: 0.1\n',
    );
    expect(result.current.feedback).toEqual({
      status: "applied",
      label: "max_name_weight",
    });
  });

  it("plans against the editor text, not the reducer source", () => {
    const editor = editorOf('schema_version: "1.1"\nrisk: {}\n');
    const { result } = renderHook(() =>
      useSourceTransactions(parsedState(SOURCE)),
    );
    act(() => result.current.onEditorReady(editor.handle));
    act(() =>
      result.current.apply(
        {
          kind: "insert-key",
          parentPointer: "/risk",
          key: "max_name_weight",
          value: 0.2,
        },
        "risk",
      ),
    );
    expect(editor.text()).toBe(
      'schema_version: "1.1"\nrisk:\n  max_name_weight: 0.2\n',
    );
  });

  it("reports failures without touching the editor", () => {
    const editor = editorOf(SOURCE);
    const { result, rerender } = renderHook(
      ({ state, active }: { state: DocumentState; active: boolean }) =>
        useSourceTransactions(state, active),
      { initialProps: { state: parsedState(SOURCE), active: true } },
    );
    act(() => result.current.onEditorReady(editor.handle));

    act(() =>
      result.current.apply({ kind: "remove", pointer: "/nope" }, "nope"),
    );
    expect(result.current.feedback).toEqual({
      status: "error",
      label: "nope",
      reason: "not-found",
    });

    rerender({
      state: { ...parsedState(SOURCE), composing: true },
      active: true,
    });
    expect(result.current.enabled).toBe(false);
    act(() =>
      result.current.apply({ kind: "remove", pointer: "/risk" }, "risk"),
    );
    expect(result.current.feedback).toEqual({
      status: "error",
      label: "risk",
      reason: "composing",
    });

    rerender({ state: parsedState(SOURCE), active: false });
    act(() =>
      result.current.apply({ kind: "remove", pointer: "/risk" }, "risk"),
    );
    expect(result.current.feedback).toEqual({
      status: "error",
      label: "risk",
      reason: "yaml-only",
    });

    act(() => result.current.onEditorReady(null));
    rerender({ state: parsedState(SOURCE), active: true });
    act(() =>
      result.current.apply({ kind: "remove", pointer: "/risk" }, "risk"),
    );
    expect(result.current.feedback).toEqual({
      status: "error",
      label: "risk",
      reason: "editor-unavailable",
    });
    expect(editor.handle.replaceRange).not.toHaveBeenCalled();
    expect(editor.text()).toBe(SOURCE);
  });

  it("is disabled while the parse lags the source, and clears feedback per document epoch", () => {
    const editor = editorOf(SOURCE);
    const stale: DocumentState = {
      ...parsedState(SOURCE),
      sourceVersion: 5,
    };
    const { result, rerender } = renderHook(
      ({ state }: { state: DocumentState }) => useSourceTransactions(state),
      { initialProps: { state: stale } },
    );
    act(() => result.current.onEditorReady(editor.handle));
    expect(result.current.enabled).toBe(false);

    rerender({ state: parsedState(SOURCE) });
    expect(result.current.enabled).toBe(true);
    act(() =>
      result.current.apply({ kind: "remove", pointer: "/risk" }, "risk"),
    );
    expect(result.current.feedback.status).toBe("applied");

    rerender({ state: { ...parsedState(SOURCE), documentEpoch: 1 } });
    expect(result.current.feedback).toEqual({ status: "idle" });
  });

  it("runs a caller-provided planner through the same editor path", () => {
    const editor = editorOf(SOURCE);
    const { result } = renderHook(() =>
      useSourceTransactions(parsedState(SOURCE)),
    );
    act(() => result.current.onEditorReady(editor.handle));
    act(() =>
      result.current.run(
        ({ text }) => ({
          status: "ok",
          edit: {
            from: text.length,
            to: text.length,
            insert: "title: t\n",
            nextSource: `${text}title: t\n`,
            selection: { from: text.length + 9, to: text.length + 9 },
          },
        }),
        "title",
      ),
    );
    expect(editor.text()).toBe(`${SOURCE}title: t\n`);
    expect(editor.handle.scrollTo).toHaveBeenCalledWith(SOURCE.length + 9);
    act(() =>
      result.current.run(
        () => ({ status: "error", reason: "duplicate" }),
        "dup",
      ),
    );
    expect(result.current.feedback).toEqual({
      status: "error",
      label: "dup",
      reason: "duplicate",
    });
  });
});
