import { useCallback, useReducer, useRef, useState } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import { documentReducer, initialDocumentState } from "../model/document-state";
import { useWholeDocumentReplacement } from "../model/replace-whole-document";
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
