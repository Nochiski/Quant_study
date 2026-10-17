import {
  useCallback,
  useEffect,
  useId,
  useRef,
} from "react";

import { t } from "../../../shared/config";
import { useCommittedRef } from "../../../shared/lib/react";
import { useBlocker } from "../../../shared/lib/router";
import { Button } from "../../../shared/ui";
import "./dirty-leave-guard.css";

const canRestoreFocus = (element: HTMLElement | null): element is HTMLElement =>
  element !== null &&
  element.isConnected &&
  element.closest("[hidden], [aria-hidden='true']") === null &&
  !element.matches(":disabled");

/**
 * 저장하지 않은 변경이 있으면 앱 안의 이동과 브라우저 이탈을 막는다(WORKFLOW P2-04).
 * 앱 안에서는 현재 편집을 계속하는 선택에 먼저 초점을 둔다. 현재 문서가 수락받은 실행의 결과로
 * 이동할 때만 수락 사실과 결과 보기 문구를 표시하고, 다른 이동에는 기존 이탈 경고를 유지한다.
 *
 * dirty는 `useCommittedRef`로 비추고 blocker는 마운트 때 한 번만 등록한다(Phase 5 backlog 21,
 * `.claude/rules/frontend-react-effects.md`): passive effect로 ref를 갱신하거나 `disabled: !dirty`로 등록을
 * 껐다 켜면, 편집으로 dirty가 켜진 commit과 그 effect 사이에 온 이동이 경고 없이 통과해 편집을 버린다.
 */
export const DirtyLeaveGuard = ({
  dirty,
  acceptedRunId = null,
}: {
  dirty: boolean;
  /** 실행 owner가 현재 문서에 대해 수락한 실행만 결과 이동 안내에 쓴다. */
  acceptedRunId?: string | null;
}) => {
  const titleId = useId();
  const descriptionId = useId();
  const latestDirty = useCommittedRef(dirty);
  const shouldBlockFn = useCallback(
    ({
      current,
      next,
    }: {
      current: { pathname: string };
      next: { pathname: string };
    }) => latestDirty.current && current.pathname !== next.pathname,
    [latestDirty],
  );
  const enableBeforeUnload = useCallback(
    () => latestDirty.current,
    [latestDirty],
  );
  const blocker = useBlocker({
    shouldBlockFn,
    enableBeforeUnload,
    withResolver: true,
  });
  const stayButton = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);
  const blocked = blocker.status === "blocked";
  useEffect(() => {
    if (!blocked) return;
    restoreFocus.current = document.activeElement as HTMLElement | null;
    queueMicrotask(() => stayButton.current?.focus());
    return () => {
      queueMicrotask(() => {
        if (canRestoreFocus(restoreFocus.current)) restoreFocus.current.focus();
      });
    };
  }, [blocked]);
  if (blocker.status !== "blocked") return null;
  // 다른 실행이나 일반 이탈에는 기존 경고를 유지한다. 경로를 파싱하거나 새 실행 상태를 만들지 않는다.
  const acceptedResult = acceptedRunId !== null &&
    blocker.next.fullPath === "/research/backtests/$runId" &&
    blocker.next.params.runId === acceptedRunId;
  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      blocker.reset();
      return;
    }
    if (event.key !== "Tab") return;
    const buttons = Array.from(
      event.currentTarget.querySelectorAll<HTMLButtonElement>("button"),
    );
    if (buttons.length === 0) return;
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const nextIndex = event.shiftKey
      ? (index - 1 + buttons.length) % buttons.length
      : (index + 1) % buttons.length;
    event.preventDefault();
    buttons[nextIndex]?.focus();
  };
  return (
    <div className="leave-guard">
      <div
        className="leave-guard__dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        onKeyDown={onKeyDown}
      >
        <p id={titleId} className="leave-guard__title">
          {t(acceptedResult ? "leave.accepted.title" : "leave.title")}
        </p>
        <p id={descriptionId}>{t(acceptedResult ? "leave.accepted.description" : "leave.description")}</p>
        <p className="leave-guard__actions">
          <Button ref={stayButton} tone="primary" onClick={blocker.reset}>
            {t(acceptedResult ? "leave.accepted.stay" : "leave.stay")}
          </Button>
          <Button onClick={blocker.proceed}>{t(acceptedResult ? "leave.accepted.leave" : "leave.leave")}</Button>
        </p>
      </div>
    </div>
  );
};
