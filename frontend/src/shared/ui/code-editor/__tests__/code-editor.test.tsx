import { undo } from "@codemirror/commands";
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

  it("hands out its handle only once the editor view exists", async () => {
    // 부모는 핸들을 받자마자 선택·포커스를 옮긴다(outline reveal). view 보다 핸들이 먼저 나가면 그
    // 명령이 조용히 사라졌다. #324 조사에서 분리해 재현한 이 준비 순서 결함을 고정한다.
    const announced: string[] = [];
    const bind = (handle: CodeEditorHandle | null) => {
      if (handle !== null) {
        announced.push(handle.getText());
        handle.setSelection(8, 9);
      }
    };
    render(
      <CodeEditor
        ref={bind}
        value={'title: "a"\n'}
        language="yaml"
        ariaLabel="편집기"
        onChange={vi.fn()}
      />,
    );
    await screen.findByRole("textbox", { name: "편집기" });
    expect(announced[0]).toBe('title: "a"\n');
    const content = screen.getByRole("textbox", { name: "편집기" });
    const view = EditorView.findFromDOM(content)!;
    expect(view.state.sliceDoc(view.state.selection.main.from, view.state.selection.main.to)).toBe("a");
  });

  it("reports text changes made through the handle and maps offsets to positions", async () => {
    const onSelectionChange = vi.fn();
    const { ref, onChange } = await mount({ onSelectionChange });
    act(() =>
      ref.current?.replaceRange(
        0,
        ref.current.getText().length,
        "a: 1\nb: 2\n",
      ),
    );
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

  it("isolates a range edit and its selection as one undoable transaction", async () => {
    const onSelectionChange = vi.fn();
    const { ref, onChange } = await mount({ onSelectionChange });
    const content = document.querySelector<HTMLElement>(".cm-content");
    const view = content ? EditorView.findFromDOM(content) : null;
    expect(view).not.toBeNull();
    act(() =>
      view!.dispatch({
        changes: { from: 7, to: 10, insert: "sig" },
        selection: { anchor: 10 },
        userEvent: "input.type",
      }),
    );

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
    act(() => expect(undo(view!)).toBe(true));
    expect(ref.current?.getText()).toBe("title: sig\n");
    expect(ref.current?.getSelection()).toEqual({ from: 10, to: 10 });
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

  it("undoes and redoes through the handle without the editor holding focus (P1-02)", async () => {
    const { ref } = await mount();
    expect(ref.current?.historyDepth()).toEqual({ undo: 0, redo: 0 });

    // Form·Graph 트랜잭션과 같은 경로: `replaceRange` 한 번은 되돌리기 한 단계다.
    act(() => ref.current?.replaceRange(7, 10, '"changed"'));
    expect(ref.current?.getText()).toBe('title: "changed"\n');
    expect(ref.current?.historyDepth()).toEqual({ undo: 1, redo: 0 });

    // 편집기는 포커스가 없다(다른 탭이 보이는 동안 hidden으로 살아 있는 상태와 같다).
    expect(document.activeElement).not.toBe(
      document.querySelector(".cm-content"),
    );
    act(() => expect(ref.current?.undo()).toBe(true));
    expect(ref.current?.getText()).toBe('title: "a"\n');
    expect(ref.current?.historyDepth()).toEqual({ undo: 0, redo: 1 });
    act(() => expect(ref.current?.undo()).toBe(false));

    act(() => expect(ref.current?.redo()).toBe(true));
    expect(ref.current?.getText()).toBe('title: "changed"\n');
    expect(ref.current?.historyDepth()).toEqual({ undo: 1, redo: 0 });
    act(() => expect(ref.current?.redo()).toBe(false));
  });

  it("이력 변경을 구독하고 변하지 않은 깊이의 snapshot을 재사용한다", async () => {
    const { ref } = await mount();
    const editor = ref.current!;
    const listener = vi.fn();
    const unsubscribe = editor.subscribeHistory(listener);
    const initial = editor.historyDepth();
    act(() => editor.setSelection(0, 0));
    expect(editor.historyDepth()).toBe(initial);
    expect(listener).not.toHaveBeenCalled();

    act(() => editor.replaceRange(0, editor.getText().length, "changed\n"));
    expect(listener).toHaveBeenCalledTimes(1);
    expect(editor.historyDepth()).toEqual({ undo: 1, redo: 0 });
    const saved = editor.getHistoryState();
    act(() => editor.undo());
    expect(editor.historyDepth()).toEqual({ undo: 0, redo: 1 });
    act(() => editor.loadText("another document\n"));
    expect(editor.historyDepth()).toEqual({ undo: 0, redo: 0 });
    act(() => editor.restoreHistoryState(saved));
    expect(editor.historyDepth()).toEqual({ undo: 1, redo: 0 });
    expect(listener).toHaveBeenCalledTimes(4);

    unsubscribe();
    act(() => editor.redo());
    expect(listener).toHaveBeenCalledTimes(4);
    act(() => editor.undo());
    expect(editor.historyDepth()).toEqual({ undo: 0, redo: 1 });
    expect(listener).toHaveBeenCalledTimes(4);
  });

  it("round-trips undo history through the opaque state", async () => {
    const { ref } = await mount();
    act(() =>
      ref.current?.replaceRange(0, ref.current.getText().length, "changed\n"),
    );
    const snapshot = ref.current?.getHistoryState();
    expect(snapshot).toBeTruthy();
    act(() => ref.current?.restoreHistoryState(snapshot));
    expect(ref.current?.getText()).toBe("changed\n");
  });
});
