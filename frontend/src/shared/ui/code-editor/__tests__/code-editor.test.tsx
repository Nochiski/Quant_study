import { EditorView } from "@codemirror/view";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CodeEditor, type CodeEditorHandle } from "..";

afterEach(cleanup);

const mount = async (props: Partial<Parameters<typeof CodeEditor>[0]> = {}) => {
  const ref = createRef<CodeEditorHandle>();
  const onChange = vi.fn();
  render(
    <CodeEditor
      ref={ref}
      value={'title: "a"\n'}
      language="yaml"
      ariaLabel="편집기"
      onChange={onChange}
      {...props}
    />,
  );
  await screen.findByRole("textbox", { name: "편집기" });
  await waitFor(() => expect(ref.current?.getText()).not.toBe(""));
  return { ref, onChange };
};

describe("CodeEditor", () => {
  it("loads lazily behind a status fallback and exposes the handle", async () => {
    const { ref } = await mount();
    expect(ref.current?.getText()).toBe('title: "a"\n');
    expect(screen.getByRole("textbox", { name: "편집기" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "Tab 키는 들여쓰기입니다. Esc 키로 편집기를 벗어납니다.",
      ),
    ).toBeInTheDocument();
  });

  it("reports text changes made through the handle and maps offsets to positions", async () => {
    const onSelectionChange = vi.fn();
    const { ref, onChange } = await mount({ onSelectionChange });
    act(() => ref.current?.setText("a: 1\nb: 2\n"));
    expect(onChange).toHaveBeenLastCalledWith("a: 1\nb: 2\n", false);
    expect(ref.current?.offsetToPosition(6)).toEqual({ line: 1, column: 1 });
    expect(ref.current?.positionToOffset({ line: 1, column: 1 })).toBe(6);
    act(() => ref.current?.setSelection(2, 4));
    expect(ref.current?.getSelection()).toEqual({ from: 2, to: 4 });
    expect(onSelectionChange).toHaveBeenLastCalledWith({
      from: 2,
      to: 4,
      documentChanged: false,
    });
  });

  it("reports an edit-owned selection without assigning source semantics", async () => {
    const onSelectionChange = vi.fn();
    await mount({ onSelectionChange });
    const content = document.querySelector<HTMLElement>(".cm-content");
    const view = content ? EditorView.findFromDOM(content) : null;
    expect(view).not.toBeNull();
    act(() =>
      view!.dispatch({
        changes: { from: 0, insert: "x" },
        selection: { anchor: 1 },
      }),
    );
    expect(onSelectionChange).toHaveBeenLastCalledWith({
      from: 1,
      to: 1,
      documentChanged: true,
    });
  });

  it("applies a range edit and its selection as one editor transaction", async () => {
    const onSelectionChange = vi.fn();
    const { ref, onChange } = await mount({ onSelectionChange });

    act(() =>
      ref.current?.replaceRange(7, 10, '"changed"', {
        from: 16,
      }),
    );

    expect(ref.current?.getText()).toBe('title: "changed"\n');
    expect(ref.current?.getSelection()).toEqual({ from: 16, to: 16 });
    expect(onChange).toHaveBeenLastCalledWith('title: "changed"\n', false);
    expect(onSelectionChange).toHaveBeenLastCalledWith({
      from: 16,
      to: 16,
      documentChanged: true,
    });
  });

  it("mirrors IME composition and marks diagnostics", async () => {
    const onComposingChange = vi.fn();
    const { ref } = await mount({
      onComposingChange,
      diagnostics: [
        {
          from: 0,
          to: 5,
          severity: "error",
          message: "bad",
          code: "yaml.syntax",
        },
      ],
    });
    const textbox = screen.getByRole("textbox", { name: "편집기" });
    act(() => {
      textbox.dispatchEvent(new Event("compositionstart", { bubbles: true }));
    });
    expect(onComposingChange).toHaveBeenLastCalledWith(true);
    act(() => {
      textbox.dispatchEvent(new Event("compositionend", { bubbles: true }));
    });
    expect(onComposingChange).toHaveBeenLastCalledWith(false);
    await waitFor(() =>
      expect(document.querySelector(".cm-lintRange-error")).not.toBeNull(),
    );
    expect(ref.current?.getText()).toBe('title: "a"\n');
  });

  it("round-trips undo history through the opaque state", async () => {
    const { ref } = await mount();
    act(() => ref.current?.setText("changed\n"));
    const snapshot = ref.current?.getHistoryState();
    expect(snapshot).toBeTruthy();
    act(() => ref.current?.restoreHistoryState(snapshot));
    expect(ref.current?.getText()).toBe("changed\n");
  });
});
