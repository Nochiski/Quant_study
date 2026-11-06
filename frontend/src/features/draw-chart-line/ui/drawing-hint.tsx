import { chartErrorSentence } from "../../../entities/chart";
import { t, tFill, type MessageKey } from "../../../shared/config";
import { failureReason } from "../../../shared/api";
import { Button, FailureNotice } from "../../../shared/ui";
import type { DrawingState } from "../model/drawing";
import type { ChartDrawing } from "../model/use-chart-drawing";
import type { useLineRemoval } from "../model/use-line-removal";

const STEP_HINTS: Record<Exclude<DrawingState["tool"], "select" | "hline">, readonly MessageKey[]> = {
  fib_retracement: ["chart.hint.fib_retracement.0", "chart.hint.fib_retracement.1"],
  fib_channel: ["chart.hint.fib_channel.0", "chart.hint.fib_channel.1", "chart.hint.fib_channel.2"],
};

const hintKey = (state: DrawingState): MessageKey =>
  state.tool === "select"
    ? "chart.hint.select"
    : state.tool === "hline"
      ? "chart.hint.hline"
      : STEP_HINTS[state.tool][Math.min(state.points.length, STEP_HINTS[state.tool].length - 1)];

/**
 * 차트 아래 안내 줄: 지금 도구에서 무엇을 누를지, 마그넷 상태. 미리보기 거절(진행 중 봉·기준점 순서)은 서버 코드의
 * 문장으로 바로 알린다. 저장 실패는 접힌 서버 사유와 함께 보인다.
 */
export const DrawingHint = ({ drawing }: { drawing: ChartDrawing }) => {
  const previewError = drawing.preview.error;
  return (
    <div className="drawing-hint">
      <p className="drawing-hint__text" aria-live="polite">
        <strong>{t(hintKey(drawing.state))}</strong>{" "}
        {drawing.state.tool === "select"
          ? null
          : t(drawing.state.magnet ? "chart.hint.magnetOn" : "chart.hint.magnetOff")}
        {drawing.saving ? ` ${t("chart.hint.saving")}` : null}
      </p>
      {previewError === null ? null : (
        <p className="drawing-hint__problem" role="status">
          {chartErrorSentence(previewError)}
        </p>
      )}
      {drawing.saveError === null ? null : (
        <FailureNotice
          title={t("chart.error.save")}
          message={chartErrorSentence(drawing.saveError)}
          reason={failureReason(drawing.saveError)}
        />
      )}
    </div>
  );
};

/** 지운 뒤의 되돌리기 알림. Ctrl+Z도 같은 일을 한다(`useLineRemoval`). */
export const UndoNotice = ({ removal }: { removal: ReturnType<typeof useLineRemoval> }) => {
  const { notice } = removal;
  if (notice === null && removal.error === null) return null;
  return (
    <div className="undo-notice" role="status">
      {notice === null ? null : (
        <>
          <span className="undo-notice__text">
            {notice.kind === "deleted"
              ? tFill("chart.undo.deleted", { title: notice.title })
              : t("chart.undo.restored")}
          </span>
          {notice.kind === "deleted" && removal.canUndo ? (
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
