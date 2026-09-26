import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  documentReducer,
  initialDocumentState,
  type DocumentState,
} from "../model/document-state";
import type { AssistantProposalApply } from "../model/use-apply-assistant-proposal";
import { useStrategyAssistant } from "../model/use-strategy-assistant";

afterEach(cleanup);

const BASE = 'schema_version: "1.1"\ntitle: "old"\n';

const fakeApply = (): AssistantProposalApply => ({
  status: { kind: "idle" },
  onEditorReady: vi.fn(),
  readSource: () => BASE,
  apply: vi.fn(),
  preview: vi.fn(),
  confirm: vi.fn(),
  cancel: vi.fn(),
});

const action = (sourceText: string) => ({
  proposal: { source_text: sourceText },
  baseSourceText: BASE,
});

const mount = (apply: AssistantProposalApply, draftId: string | null) =>
  renderHook(
    ({ state }: { state: DocumentState }) =>
      useStrategyAssistant(apply, state, {
        draftId,
        environment: null,
        backtest: { canRun: false, settling: false, run: vi.fn() },
      }),
    { initialProps: { state: initialDocumentState("yaml", BASE) } },
  );

describe("useStrategyAssistant", () => {
  it("제안 카드의 세 동작을 적용 훅으로 보낸다", () => {
    const apply = fakeApply();
    const { result } = mount(apply, "draft-1");
    const expected = { source: "next", baseSource: BASE };

    act(() =>
      result.current.proposalHandlers.onPreviewProposal(action("next")),
    );
    expect(apply.preview).toHaveBeenCalledWith(expected);

    act(() => result.current.proposalHandlers.onApplyProposal(action("next")));
    expect(apply.apply).toHaveBeenCalledWith(expected);

    // "적용 후 백테스트"도 적용은 같은 경로를 탄다 — 실행은 검증이 끝난 뒤 체인이 잇는다.
    act(() =>
      result.current.proposalHandlers.onApplyProposalAndBacktest(
        action("next"),
      ),
    );
    expect(apply.apply).toHaveBeenCalledTimes(2);
  });

  it("문서 참조는 타자마다 바뀌지 않는다", () => {
    const { result, rerender } = mount(fakeApply(), "draft-1");
    const first = result.current.documentRef;
    expect(first).toEqual({ draft_id: "draft-1" });

    rerender({
      state: documentReducer(initialDocumentState("yaml", BASE), {
        type: "edit",
        source: 'schema_version: "1.1"\ntitle: "typed"\n',
      }),
    });
    expect(result.current.documentRef).toBe(first);
  });

  it("턴 컨텍스트는 편집기의 지금 텍스트를 싣는다", () => {
    const { result } = mount(fakeApply(), null);
    expect(result.current.readContext().source_text).toBe(BASE);
  });
});
