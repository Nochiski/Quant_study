import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { CodeEditor, type CodeEditorHandle } from "..";

// jsdom has no layout: CodeMirror needs these to measure the viewport.
beforeAll(() => {
  const rect = () => ({
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    bottom: 0,
    right: 0,
    width: 0,
    height: 0,
    toJSON: () => ({}),
  });
  Range.prototype.getClientRects = () =>
    ({
      length: 0,
      item: () => null,
      [Symbol.iterator]: [][Symbol.iterator],
    }) as unknown as DOMRectList;
  Range.prototype.getBoundingClientRect = rect as unknown as () => DOMRect;
  document.createRange = () => {
    const range = new Range();
    range.getBoundingClientRect = rect as unknown as () => DOMRect;
    range.getClientRects = () =>
      ({
        length: 0,
        item: () => null,
        [Symbol.iterator]: [][Symbol.iterator],
      }) as unknown as DOMRectList;
    return range;
  };
});

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
    const { ref, onChange } = await mount();
    act(() => ref.current?.setText("a: 1\nb: 2\n"));
    expect(onChange).toHaveBeenLastCalledWith("a: 1\nb: 2\n", false);
    expect(ref.current?.offsetToPosition(6)).toEqual({ line: 1, column: 1 });
    expect(ref.current?.positionToOffset({ line: 1, column: 1 })).toBe(6);
    act(() => ref.current?.setSelection(2, 4));
    expect(ref.current?.getSelection()).toEqual({ from: 2, to: 4 });
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
