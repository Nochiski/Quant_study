import { useEffect, useId, useRef } from "react";

import { t } from "../../../shared/config";
import { useBlocker } from "../../../shared/lib/router";
import { Button } from "../../../shared/ui";
import "./dirty-leave-guard.css";

const canRestoreFocus = (element: HTMLElement | null): element is HTMLElement =>
  element !== null &&
  element.isConnected &&
  element.closest("[hidden], [aria-hidden='true']") === null &&
  !element.matches(":disabled");

/**
 * Blocks in-app navigation (and the browser's unload) while the draft has unsaved changes
 * (WORKFLOW P2-04). The prompt is an in-page alert dialog so the wording is ours and testable;
 * "머무르기" is the default action because leaving discards work.
 */
export const DirtyLeaveGuard = ({ dirty }: { dirty: boolean }) => {
  const titleId = useId();
  const descriptionId = useId();
  const latestDirty = useRef(dirty);
  useEffect(() => {
    latestDirty.current = dirty;
  }, [dirty]);
  const blocker = useBlocker({
    shouldBlockFn: ({ current, next }) =>
      latestDirty.current && current.pathname !== next.pathname,
    enableBeforeUnload: () => latestDirty.current,
    disabled: !dirty,
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
          {t("leave.title")}
        </p>
        <p id={descriptionId}>{t("leave.description")}</p>
        <p className="leave-guard__actions">
          <Button ref={stayButton} tone="primary" onClick={blocker.reset}>
            {t("leave.stay")}
          </Button>
          <Button onClick={blocker.proceed}>{t("leave.leave")}</Button>
        </p>
      </div>
    </div>
  );
};
