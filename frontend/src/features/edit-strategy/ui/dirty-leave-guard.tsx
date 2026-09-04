import { useId } from "react";

import { t } from "../../../shared/config";
import { useBlocker } from "../../../shared/lib/router";
import { Button } from "../../../shared/ui";
import "./dirty-leave-guard.css";

/**
 * Blocks in-app navigation (and the browser's unload) while the draft has unsaved changes
 * (WORKFLOW P2-04). The prompt is an in-page alert dialog so the wording is ours and testable;
 * "머무르기" is the default action because leaving discards work.
 */
export const DirtyLeaveGuard = ({ dirty }: { dirty: boolean }) => {
  const titleId = useId();
  const descriptionId = useId();
  const blocker = useBlocker({
    shouldBlockFn: () => dirty,
    enableBeforeUnload: () => dirty,
    disabled: !dirty,
    withResolver: true,
  });
  if (blocker.status !== "blocked") return null;
  return (
    <div className="leave-guard">
      <div
        className="leave-guard__dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
      >
        <p id={titleId} className="leave-guard__title">
          {t("leave.title")}
        </p>
        <p id={descriptionId}>{t("leave.description")}</p>
        <p className="leave-guard__actions">
          <Button tone="primary" autoFocus onClick={blocker.reset}>
            {t("leave.stay")}
          </Button>
          <Button onClick={blocker.proceed}>{t("leave.leave")}</Button>
        </p>
      </div>
    </div>
  );
};
