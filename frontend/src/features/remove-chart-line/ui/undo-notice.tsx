import { failureReason } from "../../../shared/api";
import { t, tFill, type MessageKey } from "../../../shared/config";
import { Button, FailureNotice } from "../../../shared/ui";
import { chartErrorSentence } from "../../../entities/chart";
import type { LineRemoval, RemovalNotice } from "../model/use-line-removal";
import "./remove-chart-line.css";

const noticeKey = (notice: RemovalNotice): MessageKey => {
  if (notice.kind === "restored") return "chart.undo.restored";
  if (!notice.lost) return "chart.undo.deleted";
  return notice.undoable ? "chart.undo.deletedPartial" : "chart.undo.deletedLost";
};

/** 지운 뒤의 되돌리기 알림. Ctrl+Z도 같은 일을 한다(`useLineRemoval`). */
export const UndoNotice = ({ removal }: { removal: LineRemoval }) => {
  const { notice } = removal;
  if (notice === null && removal.error === null) return null;
  return (
    <div className="undo-notice" role="status">
      {notice === null ? null : (
        <>
          <span className="undo-notice__text">
            {tFill(noticeKey(notice), { title: notice.title })}
          </span>
          {notice.kind === "deleted" && notice.undoable && removal.canUndo ? (
            <Button size="small" tone="ghost" className="undo-notice__undo" onClick={() => removal.undo()}>
              {t("chart.undo.action")}
            </Button>
          ) : null}
          <Button size="small" tone="ghost" className="undo-notice__undo" onClick={removal.dismissNotice}>
            {t("chart.undo.dismiss")}
          </Button>
        </>
      )}
      {removal.error === null ? null : (
        <FailureNotice
          title={t("chart.error.save")}
          message={chartErrorSentence(removal.error)}
          reason={failureReason(removal.error)}
        />
      )}
    </div>
  );
};
