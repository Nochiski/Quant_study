import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CodeEditorHandle } from "../../../shared/ui";
import {
  initialDocumentState,
  type DocumentDiagnostic,
} from "../model/document-state";
import { migrateStrategyView } from "../model/strategy-views";
import { useDiagnosticNavigation } from "../model/use-diagnostic-navigation";

afterEach(cleanup);
const diagnostic: DocumentDiagnostic = {
  code: "source.syntax",
  kind: "syntax",
  severity: "error",
  pointer: "",
  message: "구문 오류",
  range: {
    start: { line: 1, column: 1, offset: 1 },
    end: { line: 1, column: 3, offset: 3 },
  },
};
const handle = (): CodeEditorHandle => ({
  getText: () => "title: value",
  loadText: vi.fn(),
  replaceRange: vi.fn(),
  getSelection: () => ({ from: 0, to: 0 }),
  setSelection: vi.fn(),
  offsetToPosition: () => ({ line: 1, column: 1 }),
  positionToOffset: () => 0,
  scrollTo: vi.fn(),
  focus: vi.fn(),
  undo: () => false,
  redo: () => false,
  historyDepth: () => ({ undo: 0, redo: 0 }),
  getHistoryState: () => null,
  restoreHistoryState: vi.fn(),
});

describe("P4-04 표현 전환", () => {
  it.each([
    [undefined, "graph"],
    ["form", "graph"],
    ["graph", "graph"],
    ["json", "yaml"],
    ["yaml", "yaml"],
    ["diff", "graph"],
  ])("옛 표현 %s을 %s로 옮긴다", (value, expected) =>
    expect(migrateStrategyView(value)).toBe(expected),
  );
  it.each([false, true])(
    "편집기가 늦게 준비되면 문제 범위를 보존한다 (다른 문서=%s)",
    (changedDocument) => {
      const open = vi.fn();
      const { result, rerender } = renderHook(
        ({ view, epoch }: { view: "graph" | "yaml"; epoch: number }) =>
          useDiagnosticNavigation({
            state: { ...initialDocumentState(), documentEpoch: epoch },
            view,
            sourceView: "yaml",
            form: null,
            tree: {},
            schema: null,
            onSelectPointer: vi.fn(),
            onOpenSource: open,
          }),
        { initialProps: { view: "graph", epoch: 1 } },
      );
      act(() => result.current.selectDiagnostic(diagnostic));
      expect(open).toHaveBeenCalledWith(undefined);
      rerender({ view: "yaml", epoch: changedDocument ? 2 : 1 });
      const editor = handle();
      act(() => result.current.onEditorReady(editor));
      if (changedDocument) expect(editor.setSelection).not.toHaveBeenCalled();
      else {
        expect(editor.setSelection).toHaveBeenCalledWith(1, 3);
        expect(editor.focus).toHaveBeenCalledOnce();
      }
    },
  );
});
