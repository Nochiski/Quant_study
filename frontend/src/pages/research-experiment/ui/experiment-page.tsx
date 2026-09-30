import { useQuery } from "@tanstack/react-query";

import {
  backtestErrorSentence,
  requestRejectionMessage,
} from "../../../entities/backtest";
import {
  experimentQuery,
  experimentStatusLabel,
  experimentStatusTone,
  experimentTrialsQuery,
  experimentWalkForwardQuery,
  trialStatusLabel,
  trialStatusTone,
  useExperimentProgress,
  useRetryExperimentTrial,
  walkForwardGapLabel,
  type ExperimentTrialState,
} from "../../../entities/experiment";
import { failureReason } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { Link, useParams } from "../../../shared/lib/router";
import { Badge, Button, FailureNotice } from "../../../shared/ui";
import "../../../shared/ui/data-list.css";

const valuesText = (values: Record<string, unknown>) =>
  Object.entries(values)
    .map(([name, value]) => `${name} ${String(value)}`)
    .join(" · ") || "—";

/** 접수 거절 코드의 문장. 백테스트 시작 거절과 같은 `backtest.error.<code>` 체계다. */
const rejectionText = (code: string | null | undefined) =>
  backtestErrorSentence(code ?? null) ?? t("experiments.trial.rejected");

const RunCell = ({ state }: { state: ExperimentTrialState }) => {
  const latest = state.attempts.at(-1);
  if (latest === undefined) return <>—</>;
  if (latest.run_id === null || latest.run_id === undefined)
    return <>{rejectionText(latest.error_code)}</>;
  return (
    <Link to="/research/backtests/$runId" params={{ runId: latest.run_id }}>
      <code>{latest.run_id.slice(0, 12)}</code>
    </Link>
  );
};

const WalkForward = ({
  experimentId,
  trials,
}: {
  experimentId: string;
  trials: ExperimentTrialState[];
}) => {
  const report = useQuery(experimentWalkForwardQuery(experimentId));
  if (report.data === undefined) return null;
  const { data } = report;
  return (
    <section aria-labelledby={`${experimentId}-walk-forward`}>
      <h2 id={`${experimentId}-walk-forward`}>
        {t("experiments.walkForward.title")}
      </h2>
      <p>
        {data.gap === null || data.gap === undefined
          ? tFill("experiments.walkForward.summary", {
              sharpe: data.out_of_sample_sharpe?.toFixed(4) ?? "—",
              retention:
                data.retention === null || data.retention === undefined
                  ? "—"
                  : `${(data.retention * 100).toFixed(1)}%`,
            })
          : walkForwardGapLabel(data.gap)}
      </p>
      <div className="data-list-page__scroll">
        <table className="data-list-page__table">
          <caption className="sr-only">
            {t("experiments.walkForward.title")}
          </caption>
          <thead>
            <tr>
              <th scope="col">{t("experiments.new.window")}</th>
              <th scope="col">{t("experiments.walkForward.pick")}</th>
              <th scope="col">{t("experiments.walkForward.trainScore")}</th>
              <th scope="col">{t("experiments.walkForward.test")}</th>
            </tr>
          </thead>
          <tbody>
            {data.windows.map(({ pick, run_status, gap }) => {
              const picked =
                pick.trial_index === null || pick.trial_index === undefined
                  ? undefined
                  : trials[pick.trial_index];
              return (
                <tr key={pick.window_index}>
                  <td>{pick.window_index + 1}</td>
                  <td>
                    {picked === undefined
                      ? "—"
                      : valuesText(picked.trial.parameter_values)}
                  </td>
                  <td>{pick.train_sharpe?.toFixed(4) ?? "—"}</td>
                  <td>
                    {pick.run_id === null || pick.run_id === undefined ? (
                      pick.error_code === null ||
                      pick.error_code === undefined ? (
                        "—"
                      ) : (
                        rejectionText(pick.error_code)
                      )
                    ) : (
                      <Link
                        to="/research/backtests/$runId"
                        params={{ runId: pick.run_id }}
                      >
                        <code>{pick.run_id.slice(0, 12)}</code> ·{" "}
                        {run_status ?? "—"}
                      </Link>
                    )}
                    {gap === null || gap === undefined ? null : (
                      <>
                        <br />
                        {walkForwardGapLabel(gap)}
                      </>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
};

/**
 * 실험 모니터(검증 랩 V5-01, US-SM-15). 상태·trial 상태·재시도 가능 여부·워크포워드 결과는 backend 응답이고,
 * 진행 스트림이 바뀔 때마다 다시 읽는다. 후보 탐색 히트맵·선택은 V5-04 다.
 */
export const ExperimentPage = () => {
  const { experimentId } = useParams({
    from: "/research/experiments/$experimentId",
  });
  const experiment = useQuery(experimentQuery(experimentId));
  const trials = useQuery(experimentTrialsQuery(experimentId));
  const retry = useRetryExperimentTrial(experimentId);
  useExperimentProgress(experimentId, experiment.data?.finished === false);
  if (experiment.isPending || trials.isPending)
    return (
      <p className="page page-state" role="status">
        {t("page.loading")}
      </p>
    );
  if (experiment.isError || trials.isError)
    return (
      <div className="page page-state page-state--error" role="alert">
        <p>{t("experiments.detail.error")}</p>
      </div>
    );
  const { record, trial_counts: counts } = experiment.data;
  return (
    <section className="page data-list-page" aria-labelledby="experiment-title">
      <header className="data-list-page__header">
        <div>
          <h1 id="experiment-title">
            {tFill("experiments.detail.title", { id: experimentId })}
          </h1>
          <p>
            <Badge tone={experimentStatusTone(experiment.data.status)}>
              {experimentStatusLabel(experiment.data.status)}
            </Badge>{" "}
            {tFill("experiments.counts", {
              completed: counts.completed ?? 0,
              total: trials.data.length,
              running: counts.running ?? 0,
              failed: counts.failed ?? 0,
            })}
          </p>
        </div>
        <span className="data-list-page__actions">
          <Link
            className="ui-button ui-button--ghost ui-button--small"
            to="/research/experiments"
            search={{}}
          >
            {t("experiments.detail.back")}
          </Link>
          <Link
            className="ui-button ui-button--secondary ui-button--small"
            to="/research/experiments/new"
            search={{ from: record.experiment_id }}
          >
            {t("experiments.again")}
          </Link>
        </span>
      </header>
      <div className="data-list-page__panel">
        {retry.isError ? (
          <FailureNotice
            title={t("experiments.detail.retryFailed")}
            message={requestRejectionMessage(
              retry.error,
              "experiments.controlFailed",
            )}
            reason={failureReason(retry.error)}
          />
        ) : null}
        <div className="data-list-page__scroll">
          <table className="data-list-page__table">
            <caption className="sr-only">
              {t("experiments.detail.trials")}
            </caption>
            <thead>
              <tr>
                <th scope="col">{t("experiments.detail.trial")}</th>
                <th scope="col">{t("experiments.new.parameter")}</th>
                <th scope="col">{t("experiments.new.train")}</th>
                <th scope="col">{t("experiments.status")}</th>
                <th scope="col">{t("experiments.detail.run")}</th>
                <th scope="col">{t("history.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {trials.data.map((state) => (
                <tr key={state.trial.index}>
                  <td>#{state.trial.index + 1}</td>
                  <td>{valuesText(state.trial.parameter_values)}</td>
                  <td>
                    {state.trial.window.train_start} ~{" "}
                    {state.trial.window.train_end}
                  </td>
                  <td>
                    <Badge tone={trialStatusTone(state.status)}>
                      {trialStatusLabel(state.status)}
                    </Badge>
                  </td>
                  <td>
                    <RunCell state={state} />
                  </td>
                  <td>
                    {state.retryable ? (
                      <Button
                        size="small"
                        tone="ghost"
                        disabled={retry.isPending}
                        onClick={() => retry.mutate(state.trial.index)}
                      >
                        {t("experiments.detail.retry")}
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {record.design.measured ? (
          <WalkForward experimentId={experimentId} trials={trials.data} />
        ) : null}
      </div>
    </section>
  );
};
