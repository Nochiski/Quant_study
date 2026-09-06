import {
  useCancelBacktest,
  useStartBacktest,
  type BacktestRunSpec,
  type BacktestRunState,
} from "../../../entities/backtest";
import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import "./backtest-run-actions.css";

type BacktestRunActionsProps = {
  runId: string;
  status: BacktestRunState["status"];
  request: BacktestRunSpec | undefined;
  requestFailed?: boolean;
  onReplayed: (runId: string) => void;
};

const ACTIVE = new Set<BacktestRunState["status"]>([
  "queued",
  "running",
  "cancel_requested",
]);

/** Cancel an active run or submit the server-owned accepted request as an exact new run. */
export const BacktestRunActions = ({
  runId,
  status,
  request,
  requestFailed = false,
  onReplayed,
}: BacktestRunActionsProps) => {
  const cancel = useCancelBacktest();
  const replay = useStartBacktest();
  const active = ACTIVE.has(status);

  const rerun = async (): Promise<void> => {
    if (request === undefined) return;
    const accepted = await replay.mutateAsync(request);
    onReplayed(accepted.run.run_id);
  };

  return (
    <div
      className="backtest-run-actions"
      role="group"
      aria-label={t("backtest.actions.title")}
    >
      {active ? (
        <Button
          size="small"
          tone="danger"
          disabled={cancel.isPending || status === "cancel_requested"}
          onClick={() => void cancel.mutateAsync(runId)}
        >
          {status === "cancel_requested"
            ? t("backtest.actions.cancelling")
            : t("backtest.actions.cancel")}
        </Button>
      ) : (
        <Button
          size="small"
          tone="primary"
          disabled={request === undefined || requestFailed || replay.isPending}
          onClick={() => void rerun()}
        >
          {replay.isPending
            ? t("backtest.actions.replaying")
            : t("backtest.actions.rerun")}
        </Button>
      )}
      {cancel.isError || replay.isError || requestFailed ? (
        <span role="alert">{t("backtest.actions.error")}</span>
      ) : null}
    </div>
  );
};
