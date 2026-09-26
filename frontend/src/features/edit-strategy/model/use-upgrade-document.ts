import { useMutation } from "@tanstack/react-query";
import { useCallback, useMemo, useRef, useState } from "react";

import {
  ApiRequestError,
  strategyWorkbenchApi,
  type UpgradedDocument,
} from "../../../shared/api";
import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import type { DocumentState } from "./document-state";
import {
  decideDocumentUpgrade,
  type StoredRevisionMeta,
  type UpgradeAvailability,
} from "./document-upgrade";

/** 업그레이드 실패 사유. backend 422 코드는 그대로 통과시키고 나머지는 두 가지로 묶는다. */
export type UpgradeFailure =
  | { reason: "editor-unavailable" }
  | { reason: "composing" }
  | { reason: "request"; code: string | null; detail: string };

export type UpgradeStatus =
  | { kind: "idle" }
  | { kind: "pending" }
  | { kind: "applied" }
  | ({ kind: "failed" } & UpgradeFailure);

export type DocumentUpgrade = {
  availability: UpgradeAvailability;
  status: UpgradeStatus;
  canUpgrade: boolean;
  onEditorReady: (editor: CodeEditorHandle | null) => void;
  upgrade: () => void;
};

type ScopedStatus = { scope: string; value: UpgradeStatus };

const IDLE: UpgradeStatus = { kind: "idle" };

/**
 * 1.0 텍스트를 backend 변환으로 1.1로 바꿔 편집기에 넣는다(WORKFLOW P2-02). 응답 source는
 * `CodeEditorHandle.replaceRange` 한 번(history 격리)으로 적용되어 실행 취소 1단계가 되고, 편집기의
 * change 이벤트가 reducer `edit`로 흘러 문서는 dirty·재컴파일 흐름을 탄다. 실패하면 텍스트는 그대로다.
 *
 * 상태는 요청을 보낸 텍스트 버전에 묶인다: 사용자가 그 사이에 편집하면 이전 결과·오류는 사라진다.
 */
export const useUpgradeDocument = (
  state: DocumentState,
  stored: StoredRevisionMeta | null,
): DocumentUpgrade => {
  const editor = useRef<CodeEditorHandle | null>(null);
  const [scopedStatus, setScopedStatus] = useState<ScopedStatus | null>(null);
  // 편집(sourceVersion)뿐 아니라 저장(savedVersion)도 상태를 닫는다: 새 revision으로 저장되면
  // "다시 썼습니다" 안내가 사라진다.
  const scope = `${state.documentEpoch}:${state.sourceVersion}:${state.savedVersion}`;
  const availability = useMemo(
    () => decideDocumentUpgrade(state, stored),
    [state, stored],
  );
  const onEditorReady = useCallback((next: CodeEditorHandle | null): void => {
    editor.current = next;
  }, []);
  const setStatus = useCallback(
    (value: UpgradeStatus, owner: string = scope): void =>
      setScopedStatus({ scope: owner, value }),
    [scope],
  );
  const mutation = useMutation({
    mutationFn: (request: {
      source: string;
      format: DocumentState["format"];
    }) => strategyWorkbenchApi.upgradeStrategyDocument(request),
  });
  const { mutateAsync, isPending } = mutation;

  const upgrade = useCallback((): void => {
    if (availability.kind !== "upgradeable" || isPending) return;
    if (state.composing) {
      // 방어 분기: `canUpgrade`가 이미 composing 중에는 버튼을 비활성화하므로 UI에서는 도달하지 않는다.
      setStatus({ kind: "failed", reason: "composing" });
      return;
    }
    const current = editor.current;
    if (current === null) {
      setStatus({ kind: "failed", reason: "editor-unavailable" });
      return;
    }
    const owner = scope;
    setStatus({ kind: "pending" }, owner);
    void mutateAsync({ source: state.source, format: state.format }).then(
      (upgraded: UpgradedDocument) => {
        // 응답이 도착하기 전에 편집됐으면 그 텍스트를 덮어쓰지 않는다.
        if (editor.current !== current || current.getText() !== state.source)
          return;
        // `replaceRange`는 history를 앞뒤로 격리한다(`isolateHistory`) — 직후에 친 글자와 업그레이드가
        // 한 undo로 묶이지 않는다. 같은 문서 안의 전체 교체는 모두 이 경로다.
        current.replaceRange(0, current.getText().length, upgraded.source);
        current.scrollTo(0);
        current.focus();
        // replaceRange가 낸 change가 reducer `edit`로 이미 흘렀으므로 다음 버전이 소유자다.
        setStatus(
          { kind: "applied" },
          `${state.documentEpoch}:${state.sourceVersion + 1}:${state.savedVersion}`,
        );
      },
      (error: unknown) => {
        setStatus(
          {
            kind: "failed",
            reason: "request",
            code:
              error instanceof ApiRequestError ? (error.code ?? null) : null,
            detail:
              error instanceof ApiRequestError
                ? (error.detail ?? error.message)
                : error instanceof Error
                  ? error.message
                  : String(error),
          },
          owner,
        );
      },
    );
  }, [
    availability.kind,
    isPending,
    mutateAsync,
    scope,
    setStatus,
    state.composing,
    state.documentEpoch,
    state.format,
    state.source,
    state.sourceVersion,
    state.savedVersion,
  ]);

  const status =
    scopedStatus !== null && scopedStatus.scope === scope
      ? scopedStatus.value
      : IDLE;

  return {
    availability,
    status,
    canUpgrade:
      availability.kind === "upgradeable" &&
      !isPending &&
      status.kind !== "pending" &&
      !state.composing,
    onEditorReady,
    upgrade,
  };
};
