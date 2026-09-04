import { useEffect, useId, useRef } from "react";

import { t } from "../../../shared/config";
import { useBlocker } from "../../../shared/lib/router";
import { Button } from "../../../shared/ui";
import "./dirty-leave-guard.css";

/**
 * Blocks leaving the page (and the browser's unload) while the draft has unsaved changes
 * (WORKFLOW P2-04). Only a change of pathname counts as leaving: same-route search changes (the
 * URL-owned view/path/asOf/security state) keep the page mounted and lose nothing, so they pass.
 * The prompt is an in-page alert dialog: Escape and "머무르기" keep the user here, Tab stays inside
 * the dialog, and "나가기" proceeds. The latest `dirty` is read at block time, never from a stale
 * closure, so a page that clears the flag right before navigating is not prompted.
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
  const blocked = blocker.status === "blocked";
  useEffect(() => {
    if (blocked) stayButton.current?.focus();
  }, [blocked]);
  if (blocker.status !== "blocked") return null;
  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      blocker.reset();
      return;
    }
    if (event.key === "Tab") {
      // Two buttons only: keep focus cycling between them.
      const buttons = Array.from(
        event.currentTarget.querySelectorAll<HTMLButtonElement>("button"),
      );
      const index = buttons.indexOf(
        document.activeElement as HTMLButtonElement,
      );
      const nextIndex = event.shiftKey
        ? (index - 1 + buttons.length) % buttons.length
        : (index + 1) % buttons.length;
      event.preventDefault();
      buttons[nextIndex]?.focus();
    }
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
