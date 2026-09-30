import { useMemo } from "react";

import {
  backtestStartRejectionMessage,
  runEnvironmentFields,
  useCancelBacktest,
  useRunEnvironmentSchema,
  useStartBacktest,
  type BacktestRunSpec,
  type BacktestRunState,
} from "../../../entities/backtest";
import { ApiRequestError, failureReason } from "../../../shared/api";
import { t } from "../../../shared/config";
import { Button, FailureNotice } from "../../../shared/ui";
import { runFieldLabel } from "../model/run-settings-problems";
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

  const rerun = (): void => {
    if (request === undefined) return;
    replay.mutate(request, {
      onSuccess: (accepted) => onReplayed(accepted.run.run_id),
    });
  };
  const schema = useRunEnvironmentSchema();
  const environmentFields = useMemo(
    () =>
      schema.data === undefined ? [] : runEnvironmentFields(schema.data.schema),
    [schema.data],
  );
  const actionErrorDetail = failureReason(cancel.error ?? replay.error);
  // 재실행 거절은 시작 거절이다 — 편집기 툴바와 같은 문장 규칙(`backtestStartRejectionMessage`)을 쓴다.
  const replayRejection =
    replay.error === null
      ? null
      : backtestStartRejectionMessage(
          replay.error instanceof ApiRequestError
            ? (replay.error.code ?? null)
            : null,
          replay.error instanceof ApiRequestError &&
            replay.error.field !== undefined
            ? runFieldLabel(environmentFields, replay.error.field)
            : null,
          replay.error instanceof ApiRequestError ? replay.error.values : {},
        );

  return (
    <div
      className="backtest-run-actions"
      role="group"
      aria-label={t("backtest.actions.title")}
    >
      {active && cancel.data?.kept_by_owners === true ? (
        // 내 몫은 빠졌지만 실험이 이 실행을 쓰고 있어 계속 돈다(#382). 멈추려면 실험을 취소한다.
        <p className="backtest-run-actions__kept" role="status">
          {t("backtest.actions.keptByExperiment")}
        </p>
      ) : active ? (
        <Button
          size="small"
          tone="danger"
          disabled={cancel.isPending || status === "cancel_requested"}
          onClick={() => cancel.mutate(runId)}
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
          onClick={rerun}
        >
          {replay.isPending
            ? t("backtest.actions.replaying")
            : t("backtest.actions.rerun")}
        </Button>
      )}
      {replayRejection !== null && cancel.error === null ? (
        <FailureNotice
          title={t("backtest.actions.rerunFailed")}
          message={replayRejection}
          reason={actionErrorDetail}
        />
      ) : cancel.isError || replay.isError || requestFailed ? (
        <FailureNotice
          message={t("backtest.actions.error")}
          reason={actionErrorDetail}
        />
      ) : null}
    </div>
  );
};
