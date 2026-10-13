import { useEditorHandle } from "../model/use-editor-handle";
import { useGuardedDocumentReplacement } from "../model/replace-whole-document";
import { useCallback, useReducer, useRef, useState } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import { documentReducer, initialDocumentState } from "../model/document-state";
import { useWholeDocumentReplacement } from "./editor-harness";
import { SourceEditor } from "../ui/source-editor";
afterEach(cleanup);
const Harness = () => {
  const [state, dispatch] = useReducer(
    documentReducer,
    initialDocumentState("yaml", "title: old\n"),
  );
  const editor = useRef<CodeEditorHandle | null>(null);
  const replacement = useWholeDocumentReplacement();
  const [read, setRead] = useState("");
  const bind = replacement.onEditorReady;
  const ready = useCallback(
    (handle: CodeEditorHandle | null) => {
      editor.current = handle;
      bind(handle);
    },
    [bind],
  );
  return (
    <>
      <SourceEditor state={state} dispatch={dispatch} onEditorReady={ready} />
      <p role="status" aria-label="즉시 읽은 원문">{read}</p>
      <button
        onClick={() => {
          replacement.replace("title: recovered\n");
          setRead(editor.current!.getText());
        }}
      >
        복구하고 즉시 읽기
      </button>
      <button
        onClick={() => {
          editor.current!.undo();
          setRead(editor.current!.getText());
        }}
      >
        되돌리기
      </button>
      <button
        onClick={() => {
          dispatch({
            type: "load",
            format: "yaml",
            source: "title: other\n",
            strategyId: "other",
            baseRevision: 1,
            baseSpecHash: "hash",
          });
        }}
      >
        다른 문서
      </button>
    </>
  );
};
it("복구 명령 다음 호출이 새 원문을 즉시 읽고 한 번의 undo로 원문을 복원한다", async () => {
  render(<Harness />);
  await screen.findByRole("textbox");
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "복구하고 즉시 읽기" }));
  expect(screen.getByRole("status", { name: "즉시 읽은 원문" })).toHaveTextContent("title: recovered");
  await user.click(screen.getByRole("button", { name: "되돌리기" }));
  expect(screen.getByRole("status", { name: "즉시 읽은 원문" })).toHaveTextContent("title: old");
  await user.click(screen.getByRole("button", { name: "복구하고 즉시 읽기" }));
  await user.click(screen.getByRole("button", { name: "다른 문서" }));
  await user.click(screen.getByRole("button", { name: "되돌리기" }));
  expect(screen.getByRole("status", { name: "즉시 읽은 원문" })).toHaveTextContent("title: other");
});

it.each([
  { documentEpoch: 2 }, { savedVersion: 2 }, { strategyId: "other" },
  { baseRevision: 2 }, { baseSpecHash: "other-hash" }, { format: "json" as const },
  { composing: true },
])("외부 교체 응답은 같은 원문이어도 바뀐 문서 신원/조합 상태를 수정하지 않는다: %j", async (change) => {
  let reply = () => {};
  const response = new Promise<void>((resolve) => { reply = resolve; });
  const before = { ...initialDocumentState("yaml", '{"title":"old"}'), strategyId: "s1", baseRevision: 1, baseSpecHash: "hash" };
  let applied: boolean | null = null;
  const GuardHarness = ({ state }: { state: typeof before }) => {
    const owner = useEditorHandle();
    const replace = useGuardedDocumentReplacement(owner, state);
    return <>
      <SourceEditor state={state} dispatch={() => {}} onEditorReady={owner.onEditorReady} />
      <button onClick={() => {
        const editor = owner.read()!;
        void response.then(() => { applied = replace(state, editor, state.source, '{"title":"response"}'); });
      }}>요청</button>
    </>;
  };
  const { rerender } = render(<GuardHarness state={before} />);
  await screen.findByRole("textbox");
  await userEvent.setup().click(screen.getByRole("button", { name: "요청" }));
  rerender(<GuardHarness state={{ ...before, ...change }} />);
  await act(async () => { reply(); await response; });
  expect(applied).toBe(false);
  expect(screen.getByRole("textbox")).toHaveTextContent('{"title":"old"}');
});
