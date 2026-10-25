import { useState } from "react";
import { requestRejectionMessage } from "../../../entities/backtest";
import {
  useCapacityPreview,
  useCreateCapacity,
} from "../../../entities/experiment";
import {
  ApiRequestError,
  failureReason,
  type BacktestRunSpec,
} from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { useCommittedRef } from "../../../shared/lib/react";
import { useNavigate } from "../../../shared/lib/router";
import { Button, FailureNotice } from "../../../shared/ui";

/** 초기 자본만 바꾼 요청을 기존 API로 보낸다. 판정과 N은 서버 응답에서 읽는다. */
export const CheckCapacity = ({
  runId,
  request,
  single,
  requestError = null,
  reloadRequest,
}: {
  runId: string;
  request: BacktestRunSpec | undefined;
  single: boolean;
  requestError?: unknown;
  reloadRequest?: () => void;
}) => {
  const navigate = useNavigate();
  const currentRun = useCommittedRef(runId);
  const [form, setForm] = useState({
    runId,
    input: "1000000, 10000000, 100000000",
    confirmed: "",
  });
  const own = form.runId === runId;
  const input = own ? form.input : "1000000, 10000000, 100000000";
  const amounts = input
    .split(/[,\s]+/u)
    .filter(Boolean)
    .map(Number);
  const validInput =
    amounts.length > 0 &&
    amounts.every((value) => Number.isFinite(value) && value > 0);
  const body =
    own && form.confirmed === input && validInput && request
      ? { run: request, initial_cash: amounts }
      : null;
  const preview = useCapacityPreview(body);
  const create = useCreateCapacity();
  const gone =
    preview.error instanceof ApiRequestError && preview.error.status === 410;
  const error = own ? (create.error ?? preview.error) : null;
  return (
    <section
      className="analysis-panel"
      aria-label={t("backtest.capacity.title")}
    >
      <h3>{t("backtest.capacity.title")}</h3>
      <p>{t("backtest.capacity.meaning")}</p>
      {!single ? (
        <p>{t("backtest.capacity.single")}</p>
      ) : request === undefined ? (
        <>
          {requestError ? (
            <FailureNotice
              message={requestRejectionMessage(
                requestError,
                "backtest.capacity.requestUnavailable",
              )}
              reason={failureReason(requestError)}
            />
          ) : (
            <p role="status">{t("backtest.capacity.requestUnavailable")}</p>
          )}
          {reloadRequest &&
          requestError &&
          (!(requestError instanceof ApiRequestError) ||
            requestError.status === 0 ||
            requestError.status >= 500) ? (
            <Button onClick={reloadRequest}>
              {t("backtest.capacity.refresh")}
            </Button>
          ) : null}
        </>
      ) : (
        <>
          <details className="ui-disclosure">
            <summary>{t("backtest.capacity.title")}</summary>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (body !== null) {
                  if (!gone) void preview.refetch();
                } else setForm({ runId, input, confirmed: input });
              }}
            >
              <label>
                {t("backtest.capacity.amounts")}
                <input
                  className="ui-input"
                  disabled={create.isPending}
                  value={input}
                  onChange={(event) => {
                    create.reset();
                    setForm({
                      runId,
                      input: event.target.value,
                      confirmed: "",
                    });
                  }}
                />
              </label>
              <Button
                type="submit"
                disabled={
                  gone ||
                  !validInput ||
                  request === undefined ||
                  preview.isFetching ||
                  create.isPending
                }
              >
                {t("backtest.capacity.preview")}
              </Button>
            </form>
            {preview.isFetching ? (
              <p role="status">{t("backtest.capacity.loading")}</p>
            ) : null}
            {error ? (
              <FailureNotice
                message={requestRejectionMessage(
                  error,
                  "backtest.capacity.failed",
                )}
                reason={failureReason(error)}
              />
            ) : null}
            {body && preview.isSuccess ? (
              <>
                <p>
                  {tFill("backtest.capacity.trials", {
                    before: preview.data.trial_count,
                    after: preview.data.trial_count_after,
                    added: preview.data.new_trial_count,
                    runs: preview.data.run_count,
                  })}
                </p>
                <p>{t("backtest.capacity.trialsMeaning")}</p>
                <Button
                  disabled={create.isPending}
                  onClick={() => {
                    const owner = runId;
                    create.mutate(body, {
                      onSuccess: (experiment) => {
                        if (currentRun.current === owner)
                          void navigate({
                            to: "/research/experiments/$experimentId",
                            params: {
                              experimentId: experiment.record.experiment_id,
                            },
                          });
                      },
                    });
                  }}
                >
                  {t(
                    create.isPending
                      ? "backtest.capacity.starting"
                      : "backtest.capacity.start",
                  )}
                </Button>
              </>
            ) : null}
          </details>
        </>
      )}
    </section>
  );
};
