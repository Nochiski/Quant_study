import { useCallback, useMemo, useRef, useState } from "react";

import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import type { DocumentState } from "./document-state";

/**
 * 사이드바가 "문서에 적용"으로 넘기는 제안 한 건. 채팅 이벤트 모양을 그대로 받지 않는 이유는 이
 * 기능이 편집기 텍스트만 알면 되기 때문이다(feature가 다른 feature를 알지 않는다, spec D7).
 */
export type AssistantProposal = {
  /** 제안 YAML 전문. 문서 전체를 대신한다. */
  source: string;
  /**
   * 그 제안을 만든 턴이 시작될 때 보낸 문서 텍스트. 새로고침으로 이력에서 복구한 제안처럼 기준을
   * 모르면 null이고, 그때는 확인을 거친다(D9: 기준 텍스트는 frontend local UI state다).
   */
  baseSource: string | null;
};

/**
 * 사이드바의 제안 동작을 적용 훅의 입력으로 옮긴다. 구조형 인자라 이 feature가 채팅 feature의 타입을
 * 들여오지 않는다(feature가 feature를 import하지 않는다).
 */
export const assistantProposalOf = (action: {
  proposal: { source_text: string };
  baseSourceText: string | null;
}): AssistantProposal => ({
  source: action.proposal.source_text,
  baseSource: action.baseSourceText,
});

/**
 * 확인 창이 열린 이유. `changed`·`unknown`은 덮어쓰기 전 확인이고, `preview`는 사용자가 카드의
 * "미리보기"로 직접 연 것이다(문서는 아직 그대로다).
 */
export type ProposalApplyBlock = "changed" | "unknown" | "preview";

export type ProposalApplyFailure =
  | "editor-unavailable"
  | "composing"
  | "stale"
  /** 제안은 YAML 전문이라 JSON 문서에는 적용하지 않는다. */
  | "yaml-only";

export type ProposalApplyStatus =
  | { kind: "idle" }
  | {
      kind: "confirming";
      proposal: AssistantProposal;
      reason: ProposalApplyBlock;
      /** 확인 화면이 비교해 보여 주고, 덮어쓰기가 지울 현재 문서 텍스트. */
      currentSource: string;
    }
  | { kind: "applied" }
  | { kind: "failed"; reason: ProposalApplyFailure };

export type AssistantProposalApply = {
  status: ProposalApplyStatus;
  /** 편집기가 준비됐고 IME 조합 중이 아니다. 사이드바의 "적용" 버튼 게이트. */
  canApply: boolean;
  onEditorReady: (editor: CodeEditorHandle | null) => void;
  /**
   * 편집기의 지금 텍스트(없으면 null). 턴을 시작하는 쪽이 reducer 상태 대신 이 값을 싣는다 —
   * 세션 생성 왕복 뒤에 불려도 그 순간 사용자가 보는 텍스트여야 하기 때문이다.
   */
  readSource: () => string | null;
  /** 기준 텍스트가 현재 텍스트와 같으면 즉시 적용하고, 다르면 확인 상태로 둔다. */
  apply: (proposal: AssistantProposal) => void;
  /** 적용하지 않고 현재 문서와의 차이만 보여 준다. 거기서 바로 적용할 수도 있다. */
  preview: (proposal: AssistantProposal) => void;
  /** 확인 화면의 "그래도 덮어쓰기". */
  confirm: () => void;
  /** 확인 화면의 "취소". 텍스트를 건드리지 않는다. */
  cancel: () => void;
};

const IDLE: ProposalApplyStatus = { kind: "idle" };

/**
 * AI 제안을 편집기 문서에 적용한다(WORKFLOW B-04, spec D7·D9).
 *
 * 적용은 업그레이드 적용(`use-upgrade-document.ts`)과 같은 **전체 범위 교체 한 번**이다:
 * `replaceRange(0, length, source)`는 history를 앞뒤로 격리(`isolateHistory`)하므로 직후에 친 글자와
 * 섞이지 않고 실행 취소 한 번으로 이전 문서가 그대로 돌아온다. 이것이 source 트랜잭션 행이 허용한
 * 두 번째 예외이며(SoT 규칙), 그래서 `planSourceOperation`을 거치지 않는다.
 *
 * 제안은 수십~수백 초 걸리는 턴의 결과라 기다리는 동안 문서를 고치는 일이 흔하다. 그래서 기준 텍스트
 * (턴 시작 시점 텍스트)가 지금 텍스트와 다르면 바로 덮어쓰지 않고 확인을 거치고, 확인하는 동안 또
 * 바뀌면(`stale`) 멈춘다 — 사용자가 본 것과 다른 문서를 지우지 않기 위해서다.
 *
 * 적용 뒤 검증은 따로 부르지 않는다: `replaceRange`가 낸 change가 편집기 `onChange` → reducer `edit`로
 * 흘러 기존 parse·compile 디바운스를 그대로 타고 배지·Problems가 갱신된다.
 */
export const useApplyAssistantProposal = (
  state: DocumentState,
): AssistantProposalApply => {
  const editor = useRef<CodeEditorHandle | null>(null);
  const [editorReady, setEditorReady] = useState(false);
  const [scoped, setScoped] = useState<{
    documentEpoch: number;
    value: ProposalApplyStatus;
  } | null>(null);
  const documentEpoch = state.documentEpoch;
  const composing = state.composing;
  const format = state.format;

  const onEditorReady = useCallback((next: CodeEditorHandle | null): void => {
    editor.current = next;
    setEditorReady(next !== null);
  }, []);

  const readSource = useCallback(
    (): string | null => editor.current?.getText() ?? null,
    [],
  );

  const setStatus = useCallback(
    (value: ProposalApplyStatus): void =>
      setScoped({ documentEpoch, value }),
    [documentEpoch],
  );

  /** 전체 범위 교체 한 번. 호출 전에 조합·편집기·기준 검사를 마친 상태여야 한다. */
  const overwrite = useCallback(
    (handle: CodeEditorHandle, source: string): void => {
      handle.replaceRange(0, handle.getText().length, source);
      handle.scrollTo(0);
      handle.focus();
      setStatus({ kind: "applied" });
    },
    [setStatus],
  );

  /** 적용·미리보기가 함께 지키는 전제. 통과하면 지금 편집기 텍스트를 돌려준다. */
  const readCurrent = useCallback((): string | null => {
    if (format !== "yaml") {
      setStatus({ kind: "failed", reason: "yaml-only" });
      return null;
    }
    if (composing) {
      setStatus({ kind: "failed", reason: "composing" });
      return null;
    }
    const handle = editor.current;
    if (handle === null) {
      setStatus({ kind: "failed", reason: "editor-unavailable" });
      return null;
    }
    return handle.getText();
  }, [composing, format, setStatus]);

  const preview = useCallback(
    (proposal: AssistantProposal): void => {
      const currentSource = readCurrent();
      if (currentSource === null) return;
      setStatus({
        kind: "confirming",
        proposal,
        reason: "preview",
        currentSource,
      });
    },
    [readCurrent, setStatus],
  );

  const apply = useCallback(
    (proposal: AssistantProposal): void => {
      const currentSource = readCurrent();
      if (currentSource === null) return;
      const handle = editor.current;
      if (handle === null) return;
      if (proposal.baseSource === currentSource) {
        overwrite(handle, proposal.source);
        return;
      }
      setStatus({
        kind: "confirming",
        proposal,
        reason: proposal.baseSource === null ? "unknown" : "changed",
        currentSource,
      });
    },
    [overwrite, readCurrent, setStatus],
  );

  // 다른 문서를 열면 결과·확인 상태는 사라진다(feedback 슬롯과 같은 방식).
  const status = useMemo<ProposalApplyStatus>(
    () =>
      scoped !== null && scoped.documentEpoch === documentEpoch
        ? scoped.value
        : IDLE,
    [documentEpoch, scoped],
  );

  const confirm = useCallback((): void => {
    if (status.kind !== "confirming") return;
    const currentSource = readCurrent();
    if (currentSource === null) return;
    const handle = editor.current;
    if (handle === null) return;
    // 사용자가 확인 화면에서 본 텍스트가 아직 그대로일 때만 덮어쓴다.
    if (currentSource !== status.currentSource) {
      setStatus({ kind: "failed", reason: "stale" });
      return;
    }
    overwrite(handle, status.proposal.source);
  }, [overwrite, readCurrent, setStatus, status]);

  const cancel = useCallback((): void => setStatus(IDLE), [setStatus]);

  return {
    status,
    canApply: editorReady && !composing && format === "yaml",
    onEditorReady,
    readSource,
    apply,
    preview,
    confirm,
    cancel,
  };
};
