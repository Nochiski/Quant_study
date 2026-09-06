import {
  BacktestRunDetail,
  useBacktestRequest,
  useBacktestResult,
  useBacktestStatus,
} from "../../../entities/backtest";
import { BacktestRunActions } from "../../../features/run-backtest";
import { t } from "../../../shared/config";
import { useNavigate, useParams } from "../../../shared/lib/router";
import { Badge } from "../../../shared/ui";

const TONE = {
  queued: "info",
  running: "info",
  cancel_requested: "warn",
  cancelled: "warn",
  failed: "error",
  completed: "ok",
} as const;

/** Backtest run entry: live status while running, the full result once completed. */
export const BacktestRunPage = () => {
  const { runId } = useParams({ from: "/research/backtests/$runId" });
  const navigate = useNavigate();
  const status = useBacktestStatus(runId);
  const request = useBacktestRequest(runId);
  const completed = status.data?.status === "completed";
  const result = useBacktestResult(runId, completed);

  if (status.isPending) {
    return <p className="page-state">{t("page.loading")}</p>;
  }
  if (status.isError || !status.data) {
    return (
      <p className="page-state page-state--error" role="alert">
        {t("page.backtest.loadError")}
      </p>
    );
  }
  const state = status.data;
  return (
    <>
      <header className="page-header">
        <h1>{t("page.backtest.title")}</h1>
        <code>{state.run_id}</code>
        <span role="status" aria-label={t("page.backtest.status")}>
          <Badge tone={TONE[state.status]}>{state.status}</Badge>
        </span>
        <BacktestRunActions
          runId={runId}
          status={state.status}
          request={request.data}
          requestFailed={request.isError}
          onReplayed={(nextRunId) =>
            void navigate({
              to: "/research/backtests/$runId",
              params: { runId: nextRunId },
            })
          }
        />
      </header>
      <p
        className="page-state"
        role="status"
        aria-label={t("page.backtest.progress")}
      >
        {state.stage} · {Math.round(state.progress * 100)}% · {state.message}
      </p>
      {completed && result.isPending ? (
        <p className="page-state" role="status">
          {t("page.loading")}
        </p>
      ) : null}
      {result.isError ? (
        <p className="page-state page-state--error" role="alert">
          {t("page.backtest.resultError")}
        </p>
      ) : null}
      {result.data ? <BacktestRunDetail result={result.data} /> : null}
    </>
  );
};
