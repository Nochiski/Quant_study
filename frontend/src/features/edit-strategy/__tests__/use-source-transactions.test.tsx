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

    let applied: boolean | undefined;
    act(() => {
      applied = result.current.apply(
        {
          kind: "replace-scalar",
          pointer: "/risk/max_name_weight",
          value: 0.1,
        },
        "max_name_weight",
      );
    });

    expect(applied).toBe(true);
    expect(editor.handle.replaceRange).toHaveBeenCalledTimes(1);
    expect(editor.text()).toBe(
      'schema_version: "1.1"\nrisk:\n  max_name_weight: 0.1\n',
    );
    expect(result.current.feedback).toEqual({
      status: "applied",
      owner: "default",
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

    let applied: boolean | undefined;
    act(() => {
      applied = result.current.apply(
        { kind: "remove", pointer: "/nope" },
        "nope",
      );
    });
    expect(applied).toBe(false);
    expect(result.current.feedback).toEqual({
      status: "error",
      owner: "default",
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
      owner: "default",
      label: "risk",
      reason: "composing",
    });

    rerender({ state: parsedState(SOURCE), active: false });
    act(() =>
      result.current.apply({ kind: "remove", pointer: "/risk" }, "risk"),
    );
    expect(result.current.feedback).toEqual({
      status: "error",
      owner: "default",
      label: "risk",
      reason: "editor-inactive",
    });

    act(() => result.current.onEditorReady(null));
    rerender({ state: parsedState(SOURCE), active: true });
    act(() =>
      result.current.apply({ kind: "remove", pointer: "/risk" }, "risk"),
    );
    expect(result.current.feedback).toEqual({
      status: "error",
      owner: "default",
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
    // parse 대기(버전 지연)는 잠그지 않는다(P5-03: 계획은 편집기 live 텍스트로 세운다). 같은 버전의 parse 실패만 `syntax`.
    expect(result.current.enabled).toBe(true);
    expect(result.current.disabled).toBeNull();
    const broken = parseSource("risk: [", "yaml");
    rerender({
      state: { ...parsedState(SOURCE), parse: broken, source: "risk: [" },
    });
    expect(result.current.disabled).toBe("syntax");

    rerender({ state: parsedState(SOURCE) });
    expect(result.current.enabled).toBe(true);
    act(() =>
      result.current.apply({ kind: "remove", pointer: "/risk" }, "risk"),
    );
    expect(result.current.feedback.status).toBe("applied");

    rerender({ state: { ...parsedState(SOURCE), documentEpoch: 1 } });
    expect(result.current.feedback).toEqual({ status: "idle" });
  });

  it("keeps one feedback slot per owner so a snippet result does not erase the form's (audit R2)", () => {
    const editor = editorOf(SOURCE);
    const { result } = renderHook(() =>
      useSourceTransactions(parsedState(SOURCE)),
    );
    act(() => result.current.onEditorReady(editor.handle));
    act(() => {
      result.current.apply(
        { kind: "replace-scalar", pointer: "/risk/max_name_weight", value: 0.1 },
        "max_name_weight",
        "form",
      );
    });
    act(() => {
      result.current.apply({ kind: "remove", pointer: "/nope" }, "nope", "snippet");
    });
    expect(result.current.feedback).toMatchObject({ status: "error", owner: "snippet" });
    expect(result.current.feedbackFor("form")).toEqual({
      status: "applied",
      owner: "form",
      label: "max_name_weight",
    });
    expect(result.current.feedbackFor("snippet")).toMatchObject({
      status: "error",
      owner: "snippet",
      reason: "not-found",
    });
    expect(result.current.feedbackFor("graph")).toEqual({ status: "idle" });
  });

  it("names the first blocking reason in priority order", () => {
    const editor = editorOf(SOURCE);
    const render = (state: DocumentState, active = true) =>
      renderHook(() => useSourceTransactions(state, active));
    expect(
      render({ ...parsedState(SOURCE), format: "json" }).result.current
        .disabled,
    ).toBe("json");
    expect(render(parsedState(SOURCE), false).result.current.disabled).toBe(
      "inactive",
    );
    expect(
      render({ ...parsedState(SOURCE), composing: true }).result.current
        .disabled,
    ).toBe("composing");
    // 같은 버전의 parse가 실패했을 때만 syntax. parse가 아직 없는 초기 상태는 잠그지 않는다(P5-03).
    expect(render(parsedState("risk: [")).result.current.disabled).toBe(
      "syntax",
    );
    expect(
      render(initialDocumentState("yaml", SOURCE)).result.current.disabled,
    ).toBe("editor");
    const ready = render(parsedState(SOURCE));
    expect(ready.result.current.disabled).toBe("editor");
    act(() => ready.result.current.onEditorReady(editor.handle));
    expect(ready.result.current.disabled).toBeNull();
    expect(ready.result.current.enabled).toBe(true);
  });

  it("still applies when the reducer parse is missing or stale, because it plans on the live editor text (audit DEFECT-P3X-003)", () => {
    const editor = editorOf(SOURCE);
    const { result } = renderHook(() =>
      useSourceTransactions(initialDocumentState("yaml", SOURCE)),
    );
    act(() => result.current.onEditorReady(editor.handle));
    // reducer parse가 없어도(대기) 잠그지 않는다 — 계획은 편집기 live 텍스트로 세운다(P5-03).
    expect(result.current.enabled).toBe(true);
    act(() =>
      result.current.apply(
        {
          kind: "replace-scalar",
          pointer: "/risk/max_name_weight",
          value: 0.2,
        },
        "max_name_weight",
        "form",
      ),
    );
    expect(editor.text()).toContain("max_name_weight: 0.2");
    expect(result.current.feedback).toEqual({
      status: "applied",
      owner: "form",
      label: "max_name_weight",
    });
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
      owner: "default",
      label: "dup",
      reason: "duplicate",
    });
  });
});
