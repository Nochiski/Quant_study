import { useState } from "react";

import {
  backtestStartRejectionMessage,
  RUN_STATUS,
  useCancelBacktest,
  useRunEnvironmentSchema,
  useStartBacktest,
  type BacktestRunSpec,
  type BacktestRunState,
} from "../../../entities/backtest";
import { failureReason } from "../../../shared/api";
import { useCommittedRef } from "../../../shared/lib/react";
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
  const active = RUN_STATUS[status].active;
  const currentRun = useCommittedRef(runId);
  const [replayOwner, setReplayOwner] = useState<string | null>(null);
  const cancelError = cancel.variables === runId ? cancel.error : null;
  const replayError = replayOwner === runId ? replay.error : null;
  const cancelPending = cancel.variables === runId && cancel.isPending;
  const replayPending = replayOwner === runId && replay.isPending;

  const rerun = (): void => {
    if (request === undefined) return;
    setReplayOwner(runId);
    replay.mutate(request, {
      onSuccess: (accepted) => {
        if (currentRun.current === runId) onReplayed(accepted.run.run_id);
      },
    });
  };
  const { environmentFields } = useRunEnvironmentSchema();
  const actionErrorDetail = failureReason(cancelError ?? replayError);
  // 재실행 거절은 시작 거절이다 — 편집기 툴바와 같은 문장 규칙(`backtestStartRejectionMessage`)을 쓴다.
  const replayRejection =
    replayError === null
      ? null
      : backtestStartRejectionMessage(replayError, (field) =>
          runFieldLabel(environmentFields, field),
        );

  return (
    <div
      className="backtest-run-actions"
      role="group"
      aria-label={t("backtest.actions.title")}
    >
      {active &&
      cancel.data?.run_id === runId &&
      cancel.data.kept_by_owners === true ? (
        // 내 몫은 빠졌지만 실험이 이 실행을 쓰고 있어 계속 돈다(#382). 멈추려면 실험을 취소한다. 같은
        // 화면이 다른 run 으로 넘어가면 앞 run 의 취소 응답은 쓰지 않는다(#407 리뷰 P2-1).
        <p className="backtest-run-actions__kept" role="status">
          {t("backtest.actions.keptByExperiment")}
        </p>
      ) : active ? (
        <Button
          size="small"
          tone="danger"
          disabled={cancelPending || status === "cancel_requested"}
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
          disabled={request === undefined || requestFailed || replayPending}
          onClick={rerun}
        >
          {replayPending
            ? t("backtest.actions.replaying")
            : t("backtest.actions.rerun")}
        </Button>
      )}
      {replayRejection !== null && cancelError === null ? (
        <FailureNotice
          title={t("backtest.actions.rerunFailed")}
          message={replayRejection}
          reason={actionErrorDetail}
        />
      ) : cancelError !== null || replayError !== null || requestFailed ? (
        <FailureNotice
          message={t("backtest.actions.error")}
          reason={actionErrorDetail}
        />
      ) : null}
    </div>
  );
};
