import { useMemo } from "react";

import {
  BacktestRejection,
  backtestStartRejectionMessage,
  runEnvironmentFields,
  useCancelBacktest,
  useRunEnvironmentSchema,
  useStartBacktest,
  type BacktestRunSpec,
  type BacktestRunState,
} from "../../../entities/backtest";
import { ApiRequestError } from "../../../shared/api";
import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";
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
  const actionError = cancel.error ?? replay.error;
  // 서버 원문은 접힌 "서버 사유"로만 간다. `ApiRequestError.message`(`API request failed …`)는 개발자
  // 진단이라 쓰지 않는다(#268 리뷰 P3-3).
  const actionErrorDetail =
    actionError instanceof ApiRequestError
      ? (actionError.detail ?? actionError.diagnostic ?? null)
      : actionError instanceof Error
        ? actionError.message
        : null;
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
      {active ? (
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
        <BacktestRejection
          title={t("backtest.actions.rerunFailed")}
          message={replayRejection}
          detail={actionErrorDetail}
        />
      ) : cancel.isError || replay.isError || requestFailed ? (
        <BacktestRejection
          message={t("backtest.actions.error")}
          detail={actionErrorDetail}
        />
      ) : null}
    </div>
  );
};
