import {
  BacktestRunDetail,
  useBacktestRequest,
  useBacktestResult,
  useBacktestStatus,
  type BacktestRunState,
} from "../../../entities/backtest";
import { BacktestRunActions } from "../../../features/run-backtest";
import { t, tOptional } from "../../../shared/config";
import { useNavigate, useParams } from "../../../shared/lib/router";
import { Badge } from "../../../shared/ui";
import "./backtest-run-page.css";

const TONE = {
  queued: "info",
  running: "info",
  cancel_requested: "warn",
  cancelled: "warn",
  failed: "error",
  completed: "ok",
} as const;

type BacktestRunErrorProps = {
  status: BacktestRunState["status"];
  error: string;
  errorCode: NonNullable<BacktestRunState["error_code"]> | null;
};

/**
 * 실패 사유 표시. "failed" 배지만으로는 원인을 알 수 없다(이슈 #154). `error_code` 번역이 있으면
 * 그 복구 문구를 본문으로 두고 서버 사유는 접힌 진단 상세로 내린다 — 원문 detail 을 그대로
 * 노출하지 않는다는 `.claude/rules/frontend-api-state.md` 를 run 쪽에서도 지킨다(이슈 #158).
 * 번역이 없을 때만 서버 사유를 본문으로 쓴다. 취소와 겹친 실패는 "실행 오류" 대신 별도 라벨.
 */
const BacktestRunError = ({
  status,
  error,
  errorCode,
}: BacktestRunErrorProps) => {
  const label =
    status === "cancelled"
      ? t("page.backtest.cancelledError")
      : t("page.backtest.runError");
  const translated =
    errorCode === null ? null : tOptional(`backtest.run.error.${errorCode}`);
  return (
    <div
      className="page-state page-state--error backtest-run-error"
      role="alert"
      aria-label={label}
    >
      {label}: {translated ?? error}
      {translated === null ? null : (
        <details className="backtest-run-error__reason">
          <summary>{t("page.backtest.serverReason")}</summary>
          {error}
        </details>
      )}
    </div>
  );
};

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
        <BacktestRunError
          status={state.status}
          error={state.error}
          errorCode={state.error_code ?? null}
        />
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
