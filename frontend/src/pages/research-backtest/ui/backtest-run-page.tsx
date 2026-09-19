import {
  BacktestRunDetail,
  useBacktestRequest,
  useBacktestResult,
  useBacktestStatus,
} from "../../../entities/backtest";
import { BacktestRunActions } from "../../../features/run-backtest";
import { t, tOptional } from "../../../shared/config";
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
      {state.error ? (
        // 서버가 남긴 실패 사유를 보여 준다 — "failed" 배지만으로는 원인을 알 수 없다(이슈 #154).
        // `error_code` 가 있으면 번역된 복구 문구를 앞에 두고, 서버 사유(경로는 서버가 가린다)는
        // 진단용으로 뒤에 붙인다(이슈 #158).
        <p
          className="page-state page-state--error"
          role="alert"
          aria-label={t("page.backtest.runError")}
        >
          {t("page.backtest.runError")}:{" "}
          {(state.error_code &&
            tOptional(`backtest.error.${state.error_code}`)) ??
            null}{" "}
          {state.error}
        </p>
      ) : null}
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
