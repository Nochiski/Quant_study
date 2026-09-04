import {
  BacktestRunDetail,
  useBacktestResult,
  useBacktestStatus,
} from "../../../entities/backtest";
import { t } from "../../../shared/config";
import { useParams } from "../../../shared/lib/router";
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
  const status = useBacktestStatus(runId);
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
        <Badge tone={TONE[state.status]}>{state.status}</Badge>
      </header>
      <p className="page-state">
        {state.stage} · {Math.round(state.progress * 100)}% · {state.message}
      </p>
      {result.data ? <BacktestRunDetail result={result.data} /> : null}
    </>
  );
};
