import { undo } from "@codemirror/commands";
import { EditorView } from "@codemirror/view";
import {
  act,
  cleanup,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useCallback, useEffect, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CodeEditor,
  type CodeEditorHandle,
} from "../../../shared/ui/code-editor";
import {
  documentReducer,
  initialDocumentState,
  type DocumentState,
} from "../model/document-state";
import { useApplyAssistantProposal } from "../model/use-apply-assistant-proposal";
import { ProposalApplyDialog, ProposalApplyFeedback } from "..";

afterEach(cleanup);

const BASE = 'schema_version: "1.1"\ntitle: "old"\n';
const PROPOSED = 'schema_version: "1.1"\ntitle: "new"\n';

/**
 * 문서 하나를 가진 편집기 handle 대역. replaceRange는 텍스트를 실제로 바꾸고, 진짜 편집기처럼 구독자에게
 * 변경을 알린다 — 하네스는 그 알림으로 reducer를 `edit`시켜 텍스트 버전을 올린다.
 */
const editorOf = (initial: string) => {
  let text = initial;
  const listeners: ((next: string) => void)[] = [];
  const handle: CodeEditorHandle = {
    getText: () => text,
    setText: vi.fn(),
    replaceRange: vi.fn((from: number, to: number, insert: string) => {
      text = `${text.slice(0, from)}${insert}${text.slice(to)}`;
      listeners.forEach((listener) => listener(text));
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
  return {
    handle,
    text: () => text,
    edit: (next: string) => {
      text = next;
      listeners.forEach((listener) => listener(text));
    },
    subscribe: (listener: (next: string) => void) => {
      listeners.push(listener);
    },
  };
};

type FakeEditor = ReturnType<typeof editorOf>;

const state = (overrides: Partial<DocumentState> = {}): DocumentState => ({
  ...initialDocumentState("yaml", BASE),
  ...overrides,
});

/**
 * 페이지와 같은 흐름을 흉내 낸다: 편집기 변경 → reducer `edit`. 결과 상태의 소유자가 텍스트 버전이므로
 * (적용 뒤 버전이 하나 올라간다) 이 흐름 없이는 "적용됨"을 읽을 수 없다.
 */
const mountHook = (initial = BASE, documentState = state()) => {
  const editor = editorOf(initial);
  const hook = renderHook(
    ({ doc }: { doc: DocumentState }) => useApplyAssistantProposal(doc),
    { initialProps: { doc: documentState } },
  );
  let doc = documentState;
  editor.subscribe((next) => {
    doc = documentReducer(doc, { type: "edit", source: next });
    hook.rerender({ doc });
  });
  act(() => hook.result.current.onEditorReady(editor.handle));
  return {
    editor,
    hook,
    /** 사용자가 한 글자 더 치는 것과 같다. */
    typeMore: () => {
      doc = documentReducer(doc, { type: "edit", source: `${doc.source}#` });
      act(() => hook.rerender({ doc }));
    },
  };
};

describe("useApplyAssistantProposal", () => {
  it("기준 텍스트가 현재 텍스트와 같으면 전체 범위 교체 한 번으로 적용한다", () => {
    const { editor, hook } = mountHook();
    act(() =>
      hook.result.current.apply({ source: PROPOSED, baseSource: BASE }),
    );

    expect(editor.handle.replaceRange).toHaveBeenCalledTimes(1);
    expect(editor.handle.replaceRange).toHaveBeenCalledWith(
      0,
      BASE.length,
      PROPOSED,
    );
    expect(editor.text()).toBe(PROPOSED);
    expect(hook.result.current.status).toEqual({ kind: "applied" });
  });

  it("적용 결과는 그 뒤 편집 한 번에 걷힌다", () => {
    const { hook, typeMore } = mountHook();
    act(() =>
      hook.result.current.apply({ source: PROPOSED, baseSource: BASE }),
    );
    expect(hook.result.current.status).toEqual({ kind: "applied" });

    typeMore();
    expect(hook.result.current.status).toEqual({ kind: "idle" });
  });

  it("문서가 바뀌었으면 덮어쓰지 않고 확인을 요구한다", () => {
    const { editor, hook } = mountHook();
    editor.edit('schema_version: "1.1"\ntitle: "typed by hand"\n');
    act(() =>
      hook.result.current.apply({ source: PROPOSED, baseSource: BASE }),
    );

    expect(editor.handle.replaceRange).not.toHaveBeenCalled();
    expect(hook.result.current.status).toMatchObject({
      kind: "confirming",
      reason: "changed",
      currentSource: 'schema_version: "1.1"\ntitle: "typed by hand"\n',
    });
  });

  it("기준 텍스트를 모르면 확인을 요구한다", () => {
    const { editor, hook } = mountHook();
    act(() =>
      hook.result.current.apply({ source: PROPOSED, baseSource: null }),
    );

    expect(editor.handle.replaceRange).not.toHaveBeenCalled();
    expect(hook.result.current.status).toMatchObject({
      kind: "confirming",
      reason: "unknown",
    });
  });

  it("덮어쓰기 확인은 같은 전체 범위 교체 경로로 적용한다", () => {
    const { editor, hook } = mountHook();
    const typed = 'schema_version: "1.1"\ntitle: "typed by hand"\n';
    editor.edit(typed);
    act(() =>
      hook.result.current.apply({ source: PROPOSED, baseSource: BASE }),
    );
    act(() => hook.result.current.confirm());

    expect(editor.handle.replaceRange).toHaveBeenCalledTimes(1);
    expect(editor.handle.replaceRange).toHaveBeenCalledWith(
      0,
      typed.length,
      PROPOSED,
    );
    expect(hook.result.current.status).toEqual({ kind: "applied" });
  });

  it("취소하면 텍스트도 상태도 그대로 둔다", () => {
    const { editor, hook } = mountHook();
    editor.edit(PROPOSED.replace("new", "mine"));
    act(() =>
      hook.result.current.apply({ source: PROPOSED, baseSource: BASE }),
    );
    act(() => hook.result.current.cancel());

    expect(editor.handle.replaceRange).not.toHaveBeenCalled();
    expect(editor.text()).toBe(PROPOSED.replace("new", "mine"));
    expect(hook.result.current.status).toEqual({ kind: "idle" });
  });

  it("확인하는 동안 문서가 또 바뀌면 적용을 멈춘다", () => {
    const { editor, hook } = mountHook();
    editor.edit('schema_version: "1.1"\ntitle: "first"\n');
    act(() =>
      hook.result.current.apply({ source: PROPOSED, baseSource: BASE }),
    );
    editor.edit('schema_version: "1.1"\ntitle: "second"\n');
    act(() => hook.result.current.confirm());

    expect(editor.handle.replaceRange).not.toHaveBeenCalled();
    expect(editor.text()).toBe('schema_version: "1.1"\ntitle: "second"\n');
    expect(hook.result.current.status).toEqual({
      kind: "failed",
      reason: "stale",
    });
  });

  it("편집기가 없거나 조합 중이면 적용하지 않고 이유를 남긴다", () => {
    const withoutEditor = renderHook(() => useApplyAssistantProposal(state()));
    act(() =>
      withoutEditor.result.current.apply({
        source: PROPOSED,
        baseSource: BASE,
      }),
    );
    expect(withoutEditor.result.current.status).toEqual({
      kind: "failed",
      reason: "editor-unavailable",
    });

    const composing = mountHook(BASE, state({ composing: true }));
    act(() =>
      composing.hook.result.current.apply({
        source: PROPOSED,
        baseSource: BASE,
      }),
    );
    expect(composing.editor.handle.replaceRange).not.toHaveBeenCalled();
    expect(composing.hook.result.current.status).toEqual({
      kind: "failed",
      reason: "composing",
    });
  });

  it("미리보기는 문서를 건드리지 않고 차이만 연다", () => {
    const { editor, hook } = mountHook();
    act(() =>
      hook.result.current.preview({ source: PROPOSED, baseSource: BASE }),
    );

    expect(editor.handle.replaceRange).not.toHaveBeenCalled();
    expect(hook.result.current.status).toMatchObject({
      kind: "confirming",
      reason: "preview",
      currentSource: BASE,
    });
    // 미리보기에서 그대로 적용할 수 있다.
    act(() => hook.result.current.confirm());
    expect(editor.text()).toBe(PROPOSED);
  });

  it("JSON 문서에는 제안을 적용하지 않는다", () => {
    const { editor, hook } = mountHook(BASE, state({ format: "json" }));
    act(() =>
      hook.result.current.apply({ source: PROPOSED, baseSource: BASE }),
    );

    expect(editor.handle.replaceRange).not.toHaveBeenCalled();
    expect(hook.result.current.status).toEqual({
      kind: "failed",
      reason: "yaml-only",
    });
  });

  it("실제 편집기에서 실행 취소 한 번으로 이전 문서가 돌아온다", async () => {
    const Harness = () => {
      const apply = useApplyAssistantProposal(state());
      const onEditorReady = apply.onEditorReady;
      const bindHandle = useCallback(
        (editor: CodeEditorHandle | null) => onEditorReady(editor),
        [onEditorReady],
      );
      return (
        <>
          <CodeEditor
            ref={bindHandle}
            value={BASE}
            language="yaml"
            ariaLabel="편집기"
            onChange={() => {}}
          />
          <button
            type="button"
            onClick={() => apply.apply({ source: PROPOSED, baseSource: BASE })}
          >
            적용
          </button>
        </>
      );
    };
    const user = userEvent.setup();
    render(<Harness />);
    await screen.findByRole("textbox", { name: "편집기" });
    const content = document.querySelector<HTMLElement>(".cm-content");
    await waitFor(() => expect(content).not.toBeNull());
    const view = EditorView.findFromDOM(content!)!;
    expect(view.state.doc.toString()).toBe(BASE);

    await user.click(screen.getByRole("button", { name: "적용" }));
    expect(view.state.doc.toString()).toBe(PROPOSED);

    act(() => expect(undo(view)).toBe(true));
    expect(view.state.doc.toString()).toBe(BASE);
  });
});

const DialogHarness = ({ editor }: { editor: FakeEditor }) => {
  const [doc, setDoc] = useState(state);
  const apply = useApplyAssistantProposal(doc);
  const onEditorReady = apply.onEditorReady;
  useEffect(() => onEditorReady(editor.handle), [editor, onEditorReady]);
  // 페이지와 같은 흐름: 편집기 변경이 reducer `edit`로 흐른다.
  useEffect(
    () =>
      editor.subscribe((next) =>
        setDoc((current) =>
          documentReducer(current, { type: "edit", source: next }),
        ),
      ),
    [editor],
  );
  return (
    <>
      <button
        type="button"
        onClick={() => apply.apply({ source: PROPOSED, baseSource: BASE })}
      >
        문서에 적용
      </button>
      <ProposalApplyFeedback apply={apply} />
      <ProposalApplyDialog apply={apply} />
    </>
  );
};

const mountDialog = async (current: string) => {
  const editor = editorOf(current);
  const user = userEvent.setup();
  render(<DialogHarness editor={editor} />);
  await user.click(screen.getByRole("button", { name: "문서에 적용" }));
  return { editor, user };
};

describe("ProposalApplyDialog", () => {
  it("문서가 바뀐 경우에만 열리고 차이를 미리 보여 준다", async () => {
    const { user } = await mountDialog('schema_version: "1.1"\ntitle: "mine"\n');
    const dialog = screen.getByRole("dialog", { name: "문서가 바뀌었습니다" });

    expect(
      screen.queryByRole("table", { name: "제안과 현재 문서의 차이" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "미리보기" }));
    const table = screen.getByRole("table", {
      name: "제안과 현재 문서의 차이",
    });
    expect(table).toHaveTextContent('title: "mine"');
    expect(table).toHaveTextContent('title: "new"');
    expect(dialog).toHaveTextContent("+1 / −1 변경 항목");
  });

  it("Tab이 다이얼로그 안에서만 돌고 Escape는 취소한다", async () => {
    const { editor, user } = await mountDialog(
      'schema_version: "1.1"\ntitle: "mine"\n',
    );
    const dialog = screen.getByRole("dialog", { name: "문서가 바뀌었습니다" });
    const buttons = [
      screen.getByRole("button", { name: "미리보기" }),
      screen.getByRole("button", { name: "그래도 덮어쓰기" }),
      screen.getByRole("button", { name: "취소" }),
    ];
    await waitFor(() => expect(buttons[0]).toHaveFocus());
    await user.tab();
    expect(buttons[1]).toHaveFocus();
    await user.tab();
    expect(buttons[2]).toHaveFocus();
    await user.tab();
    expect(buttons[0]).toHaveFocus();
    await user.tab({ shift: true });
    expect(buttons[2]).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(dialog).not.toBeInTheDocument();
    expect(editor.handle.replaceRange).not.toHaveBeenCalled();
  });

  it("덮어쓰기는 문서를 바꾸고 결과를 알린다", async () => {
    const { editor, user } = await mountDialog(
      'schema_version: "1.1"\ntitle: "mine"\n',
    );
    await user.click(screen.getByRole("button", { name: "그래도 덮어쓰기" }));
    expect(editor.text()).toBe(PROPOSED);
    expect(
      screen.queryByRole("dialog", { name: "문서가 바뀌었습니다" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "제안을 문서에 적용했습니다.",
    );
  });

  it("적용을 멈춘 이유를 알림으로 남긴다", async () => {
    const { editor, user } = await mountDialog(
      'schema_version: "1.1"\ntitle: "mine"\n',
    );
    editor.edit('schema_version: "1.1"\ntitle: "again"\n');
    await user.click(screen.getByRole("button", { name: "그래도 덮어쓰기" }));
    expect(editor.handle.replaceRange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "확인하는 동안 문서가 또 바뀌어",
    );
  });
});
