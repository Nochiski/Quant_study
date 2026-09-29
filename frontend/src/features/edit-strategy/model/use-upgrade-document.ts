import { useMutation } from "@tanstack/react-query";
import { useCallback, useMemo, useRef, useState } from "react";

import {
  ApiRequestError,
  failureReason,
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

/**
 * 업그레이드 실패 사유. backend 422 코드는 그대로 통과시키고 나머지는 두 가지로 묶는다. `detail` 은 접힌
 * 서버 사유에 둘 원문(`failureReason`)이다.
 */
export type UpgradeFailure =
  | { reason: "editor-unavailable" }
  | { reason: "composing" }
  | { reason: "request"; code: string | null; detail: string | null };

export type UpgradeStatus =
  | { kind: "idle" }
  | { kind: "pending" }
  /**
   * 적용됐다. `environment` 는 옛 문서가 들고 있던 실행 설정(옮기지 못했으면 null)이고, `warnings` 는
   * 사용자가 알아야 할 동작 변화다(P2-09). 실행 설정은 사용자가 배너에서 누를 때만 패널에 들어간다.
   * `environmentFilled` 는 그렇게 채웠는지다 — 적용 결과와 같은 글에 묶여 실행 취소 → 다시 실행 뒤에도
   * 남는다(#297 리뷰 P3-3).
   */
  | {
      kind: "applied";
      environment: UpgradedDocument["environment"];
      warnings: UpgradedDocument["warnings"];
      environmentFilled: boolean;
    }
  | ({ kind: "failed" } & UpgradeFailure);

export type DocumentUpgrade = {
  availability: UpgradeAvailability;
  status: UpgradeStatus;
  canUpgrade: boolean;
  onEditorReady: (editor: CodeEditorHandle | null) => void;
  upgrade: () => void;
  /** 적용 결과의 옛 실행 설정을 실행 설정 패널에 채웠다고 적는다(배너의 "실행 설정에 채우기"). */
  markEnvironmentFilled: () => void;
};

/**
 * 상태가 묶이는 글. 편집기 글이 이 글과 같을 때만 상태가 보인다. 버전 번호에 묶으면 실행 취소 뒤 다시
 * 실행이 새 버전을 만들어, 업그레이드한 글이 돌아와도 적용 결과("실행 설정에 채우기")가 사라진다(#267
 * DEFECT-1). 같은 문서(`documentEpoch`)의 같은 저장(`savedVersion`) 안에서만 보인다 — 새 revision으로
 * 저장하면 "저장하세요" 안내가 끝난다.
 */
type StatusOwner = Pick<
  DocumentState,
  "documentEpoch" | "savedVersion" | "source"
>;

type OwnedStatus = { owner: StatusOwner; value: UpgradeStatus };

const IDLE: UpgradeStatus = { kind: "idle" };

const owns = (owner: StatusOwner, state: StatusOwner): boolean =>
  owner.documentEpoch === state.documentEpoch &&
  owner.savedVersion === state.savedVersion &&
  owner.source === state.source;

/**
 * 은퇴 버전 텍스트를 backend 변환으로 현재 버전으로 바꿔 편집기에 넣는다(WORKFLOW P2-02·P3-02). 응답 source는
 * `CodeEditorHandle.replaceRange` 한 번(history 격리)으로 적용되어 실행 취소 1단계가 되고, 편집기의
 * change 이벤트가 reducer `edit`로 흘러 문서는 dirty·재컴파일 흐름을 탄다. 실패하면 텍스트는 그대로다.
 *
 * 대기·실패는 요청을 보낸 글에, 적용 결과는 바꾼 뒤의 글에 묶인다(`StatusOwner`).
 */
export const useUpgradeDocument = (
  state: DocumentState,
  stored: StoredRevisionMeta | null,
): DocumentUpgrade => {
  const editor = useRef<CodeEditorHandle | null>(null);
  const [owned, setOwned] = useState<OwnedStatus | null>(null);
  const { composing, documentEpoch, format, savedVersion, source } = state;
  const availability = useMemo(
    () => decideDocumentUpgrade(state, stored),
    [state, stored],
  );
  const onEditorReady = useCallback((next: CodeEditorHandle | null): void => {
    editor.current = next;
  }, []);
  const mutation = useMutation({
    mutationFn: (request: {
      source: string;
      format: DocumentState["format"];
    }) => strategyWorkbenchApi.upgradeStrategyDocument(request),
  });
  const { mutateAsync, isPending } = mutation;

  const upgrade = useCallback((): void => {
    if (availability.kind !== "upgradeable" || isPending) return;
    const owner: StatusOwner = { documentEpoch, savedVersion, source };
    if (composing) {
      // 방어 분기: `canUpgrade`가 이미 composing 중에는 버튼을 비활성화하므로 UI에서는 도달하지 않는다.
      setOwned({ owner, value: { kind: "failed", reason: "composing" } });
      return;
    }
    const current = editor.current;
    if (current === null) {
      setOwned({
        owner,
        value: { kind: "failed", reason: "editor-unavailable" },
      });
      return;
    }
    setOwned({ owner, value: { kind: "pending" } });
    void mutateAsync({ source, format }).then(
      (upgraded: UpgradedDocument) => {
        // 응답이 도착하기 전에 편집됐으면 그 텍스트를 덮어쓰지 않고 대기 상태도 지운다. 대기는 요청한 글에
        // 묶여 있어, 남겨 두면 실행 취소로 그 글에 돌아왔을 때 "업그레이드 중…"이 되살아나 버튼이 멈춘다
        // (#297 리뷰 P2-1). 대기 중에는 `isPending` 이 다른 요청을 막으므로 지우는 것은 이 요청의 대기뿐이다.
        if (editor.current !== current || current.getText() !== source) {
          setOwned(null);
          return;
        }
        // `replaceRange`는 history를 앞뒤로 격리한다(`isolateHistory`) — 직후에 친 글자와 업그레이드가
        // 한 undo로 묶이지 않는다. 같은 문서 안의 전체 교체는 모두 이 경로다.
        current.replaceRange(0, current.getText().length, upgraded.source);
        current.scrollTo(0);
        current.focus();
        // 편집기가 줄 끝을 정규화하므로 응답 원문이 아니라 편집기에 들어간 글에 묶는다.
        setOwned({
          owner: { ...owner, source: current.getText() },
          value: {
            kind: "applied",
            environment: upgraded.environment,
            warnings: upgraded.warnings,
            environmentFilled: false,
          },
        });
      },
      (error: unknown) => {
        setOwned({
          owner,
          value: {
            kind: "failed",
            reason: "request",
            code:
              error instanceof ApiRequestError ? (error.code ?? null) : null,
            detail: failureReason(error),
          },
        });
      },
    );
  }, [
    availability.kind,
    composing,
    documentEpoch,
    format,
    isPending,
    mutateAsync,
    savedVersion,
    source,
  ]);

  const markEnvironmentFilled = useCallback((): void => {
    setOwned((current) =>
      current?.value.kind === "applied"
        ? { ...current, value: { ...current.value, environmentFilled: true } }
        : current,
    );
  }, []);

  const status =
    owned !== null && owns(owned.owner, state) ? owned.value : IDLE;

  return {
    availability,
    status,
    canUpgrade:
      availability.kind === "upgradeable" &&
      !isPending &&
      status.kind !== "pending" &&
      !composing,
    onEditorReady,
    upgrade,
    markEnvironmentFilled,
  };
};
