import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import {
  documentReducer,
  initialDocumentState,
  type DocumentState,
} from "../model/document-state";
import { useApplyAssistantProposal } from "../model/use-apply-assistant-proposal";
import { useApplyProposalThenBacktest } from "../model/use-apply-then-backtest";

afterEach(cleanup);

const BASE = 'schema_version: "1.1"\ntitle: "old"\n';
const PROPOSED = 'schema_version: "1.1"\ntitle: "new"\n';

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
    edit: (next: string) => {
      text = next;
      listeners.forEach((listener) => listener(text));
    },
    subscribe: (listener: (next: string) => void) => {
      listeners.push(listener);
    },
  };
};

const edited = (source: string): DocumentState =>
  documentReducer(initialDocumentState("yaml", BASE), {
    type: "edit",
    source,
  });

const parsed = (state: DocumentState): DocumentState =>
  documentReducer(state, {
    type: "parsed",
    version: state.sourceVersion,
    result: parseSource(state.source, "yaml"),
  });

const compiled = (state: DocumentState, error: boolean): DocumentState =>
  documentReducer(state, {
    type: "compiled",
    version: state.sourceVersion,
    outcome: {
      spec: null,
      canonicalJson: null,
      specHash: null,
      schemaVersion: null,
      sourceHash: "h",
      diagnostics: error
        ? [
            {
              code: "structure.missing_key",
              kind: "structural",
              severity: "error",
              pointer: "/data",
              message: "data 섹션이 필요합니다.",
              range: null,
            },
          ]
        : [],
    },
  });

/** 문서 상태와 실행 게이트를 테스트가 밀어 넣는 체인 하네스. */
const mountChain = (initial = BASE) => {
  const editor = editorOf(initial);
  const run = vi.fn();
  const hook = renderHook(
    ({ state, canRun }: { state: DocumentState; canRun: boolean }) => {
      const apply = useApplyAssistantProposal(state);
      return {
        apply,
        chain: useApplyProposalThenBacktest(apply, state, { canRun, run }),
      };
    },
    {
      initialProps: {
        state: initialDocumentState("yaml", BASE),
        canRun: false,
      },
    },
  );
  act(() => hook.result.current.apply.onEditorReady(editor.handle));
  let current = { state: initialDocumentState("yaml", BASE), canRun: false };
  const rerender = (next: { state: DocumentState; canRun: boolean }) => {
    current = next;
    hook.rerender(next);
  };
  // 페이지와 같은 흐름: 편집기 변경이 reducer `edit`로 흘러 텍스트 버전이 오른다. 같은 텍스트면
  // reducer가 상태를 그대로 돌려주므로 버전도 그대로다 — 그 사실이 여기서도 재현돼야 한다.
  editor.subscribe((text) =>
    rerender({
      state: documentReducer(current.state, { type: "edit", source: text }),
      canRun: current.canRun,
    }),
  );
  return { editor, run, hook, rerender };
};

describe("useApplyProposalThenBacktest", () => {
  it("검증이 끝나 실행 가능해지면 그때 백테스트를 잇는다", () => {
    const { run, hook, rerender } = mountChain();
    act(() =>
      hook.result.current.chain.applyThenBacktest({
        source: PROPOSED,
        baseSource: BASE,
      }),
    );
    expect(hook.result.current.chain.waiting).toBe(true);
    expect(run).not.toHaveBeenCalled();

    // 편집기 change가 reducer로 흘렀지만 검증은 아직이다.
    const typing = edited(PROPOSED);
    rerender({ state: typing, canRun: false });
    expect(hook.result.current.chain.waiting).toBe(true);
    expect(run).not.toHaveBeenCalled();

    rerender({ state: compiled(parsed(typing), false), canRun: true });
    expect(run).toHaveBeenCalledTimes(1);
    expect(hook.result.current.chain.waiting).toBe(false);
  });

  it("제안이 지금 문서와 같아도 백테스트를 잇는다", () => {
    // 같은 텍스트라 reducer가 그대로 → 텍스트 버전이 오르지 않는다. 그 경우에도 사용자가 누른 동작의
    // 뒷부분(실행)은 이어져야 한다(3차 리뷰 P2-1).
    const { run, hook, rerender } = mountChain();
    const settled = compiled(parsed(initialDocumentState("yaml", BASE)), false);
    rerender({ state: settled, canRun: true });
    act(() =>
      hook.result.current.chain.applyThenBacktest({
        source: BASE,
        baseSource: BASE,
      }),
    );

    expect(run).toHaveBeenCalledTimes(1);
    expect(hook.result.current.chain.waiting).toBe(false);
  });

  it("검증이 끝났는데 실행할 수 없으면 대기를 풀고 실행하지 않는다", () => {
    const { run, hook, rerender } = mountChain();
    act(() =>
      hook.result.current.chain.applyThenBacktest({
        source: PROPOSED,
        baseSource: BASE,
      }),
    );
    const settled = compiled(parsed(edited(PROPOSED)), true);
    rerender({ state: settled, canRun: false });

    expect(run).not.toHaveBeenCalled();
    expect(hook.result.current.chain.waiting).toBe(false);
    expect(hook.result.current.chain.waiting).toBe(false);
  });

  it("구문 오류로 검증이 시작되지 않아도 영원히 기다리지 않는다", () => {
    const { run, hook, rerender } = mountChain();
    act(() =>
      hook.result.current.chain.applyThenBacktest({
        source: "title: [\n",
        baseSource: BASE,
      }),
    );
    rerender({ state: parsed(edited("title: [\n")), canRun: false });

    expect(run).not.toHaveBeenCalled();
    expect(hook.result.current.chain.waiting).toBe(false);
    expect(hook.result.current.chain.waiting).toBe(false);
  });

  it("확인 창에서 취소하면 실행도 잇지 않는다", () => {
    const { editor, run, hook } = mountChain();
    editor.edit('schema_version: "1.1"\ntitle: "mine"\n');
    act(() =>
      hook.result.current.chain.applyThenBacktest({
        source: PROPOSED,
        baseSource: BASE,
      }),
    );
    expect(hook.result.current.apply.status.kind).toBe("confirming");
    expect(hook.result.current.chain.waiting).toBe(true);

    act(() => hook.result.current.apply.cancel());
    expect(hook.result.current.chain.waiting).toBe(false);
    expect(run).not.toHaveBeenCalled();
  });

  it("기다리는 동안 문서를 또 고치면 실행을 잇지 않는다", () => {
    const { run, hook, rerender } = mountChain();
    act(() =>
      hook.result.current.chain.applyThenBacktest({
        source: PROPOSED,
        baseSource: BASE,
      }),
    );
    // 적용한 텍스트가 reducer에 닿은 뒤, 검증이 끝나기 전에 사용자가 이어서 고친다.
    rerender({ state: edited(PROPOSED), canRun: false });
    expect(hook.result.current.chain.waiting).toBe(true);
    const typedOver = edited('schema_version: "1.1"\ntitle: "new but mine"\n');
    rerender({ state: compiled(parsed(typedOver), false), canRun: true });

    expect(run).not.toHaveBeenCalled();
    expect(hook.result.current.chain.waiting).toBe(false);
  });
});
