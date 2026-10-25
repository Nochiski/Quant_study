import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import {
  BacktestRunFailure,
  backtestHistoryQuery,
  RUN_STATUS,
  RunHeadline,
  runStageLabel,
  type BacktestRunSummary,
} from "../../../entities/backtest";
import {
  experimentStatusLabel,
  experimentStatusTone,
  experimentsQuery,
  TrialProgress,
} from "../../../entities/experiment";
import { strategiesQuery } from "../../../entities/strategy";
import { listDrafts } from "../../../features/edit-strategy";
import { t, tFill } from "../../../shared/config";
import { browserStorage } from "../../../shared/lib/browser-storage";
import { displayTime } from "../../../shared/lib/display-time";
import { Link } from "../../../shared/lib/router";
import { Badge, EmptyState } from "../../../shared/ui";
import "../../../shared/ui/data-list.css";
import { activityLog, awaitingCandidate, type ActivityEntry } from "../model/home-model";
import "./research-home-page.css";

// 홈은 최근 것만 본다. 오래된 기록은 각 목록 화면이 소유한다.
const RECENT_RUNS = 20;
const FAILURES_SHOWN = 2;
const RESULTS_SHOWN = 5;
const ACTIVITY_SHOWN = 10;

const sourceLabel = (summary: BacktestRunSummary): string => {
  const provenance = summary.strategy_provenance;
  return provenance.kind === "saved_revision" && provenance.strategy_id
    ? `${provenance.strategy_id} · v${provenance.revision}`
    : t("history.backtests.unsaved");
};

const ActivityText = ({ entry }: { entry: ActivityEntry }) => {
  switch (entry.kind) {
    case "revision":
      return (
        <Link
          to="/research/strategies/$strategyId"
          params={{ strategyId: entry.strategyId }}
        >
          {tFill("home.activity.revision", {
            title: entry.title || entry.strategyId,
            revision: entry.revision,
          })}
        </Link>
      );
    case "backtest":
      return (
        <Link to="/research/backtests/$runId" params={{ runId: entry.summary.run.run_id }}>
          {tFill(
            entry.summary.run.status === "failed"
              ? "home.activity.backtestFailed"
              : "home.activity.backtestCompleted",
            { source: sourceLabel(entry.summary) },
          )}
        </Link>
      );
    case "experiment":
      return (
        <Link
          to="/research/experiments/$experimentId"
          params={{ experimentId: entry.experimentId }}
        >
          {tFill("home.activity.experiment", { id: entry.experimentId.slice(0, 8) })}
        </Link>
      );
    case "selection":
      return (
        <Link
          to="/research/experiments/$experimentId"
          params={{ experimentId: entry.experimentId }}
        >
          {tFill("home.activity.selection", {
            source: `${entry.strategyId} · v${entry.revision}`,
          })}
        </Link>
      );
  }
};

/** 계좌 자리: 운영 기능이 없어 숫자를 그리지 않는다(RD-01-B). */
const AccountCard = () => (
  <section className="research-home__card" aria-labelledby="home-account-title">
    <h2 id="home-account-title">
      {t("home.account.title")} <Badge tone="neutral">{t("home.account.unconnected")}</Badge>
    </h2>
    <p>{t("home.account.body")}</p>
  </section>
);

export const ResearchHomePage = () => {
  // 로컬 보관함은 React 밖 상태라 화면을 열 때 한 번 읽는다.
  const [drafts] = useState(() => listDrafts(browserStorage()));
  const strategies = useQuery(strategiesQuery({ limit: ACTIVITY_SHOWN }));
  const backtests = useQuery(backtestHistoryQuery({ kind: "single", limit: RECENT_RUNS }));
  const experiments = useInfiniteQuery(experimentsQuery());
  const experimentItems = experiments.data?.pages.flatMap((page) => page.items) ?? [];
  const slots = experiments.data?.pages[0]?.slots;
  const runs = backtests.data?.items ?? [];

  if (strategies.isSuccess && strategies.data.total === 0)
    return (
      <section className="page research-home" aria-labelledby="home-title">
        <h1 id="home-title">{t("home.title")}</h1>
        <EmptyState
          title={t("home.empty.title")}
          description={t("home.empty.description")}
          action={
            <Link
              className="ui-button ui-button--primary"
              to="/research/strategies/new"
              search={{ guide: true }}
            >
              {t("history.strategies.new")}
            </Link>
          }
        />
        <AccountCard />
      </section>
    );

  const waiting = awaitingCandidate(experimentItems);
  const failures = runs.filter((summary) => summary.run.status === "failed").slice(0, FAILURES_SHOWN);
  const todoCount = waiting.length + failures.length + drafts.length;
  const runningExperiments = experimentItems.filter((experiment) => !experiment.finished);
  const runningBacktests = runs.filter((summary) => RUN_STATUS[summary.run.status].active);
  const results = runs.filter((summary) => summary.run.status === "completed").slice(0, RESULTS_SHOWN);
  const activity = activityLog(
    {
      strategies: strategies.data?.items ?? [],
      backtests: runs,
      experiments: experimentItems,
    },
    ACTIVITY_SHOWN,
  );

  return (
    <section className="page research-home" aria-labelledby="home-title">
      <h1 id="home-title">{t("home.title")}</h1>
      <div className="research-home__grid">
        <section className="research-home__card research-home__todo" aria-labelledby="home-todo-title">
          <h2 id="home-todo-title">
            {todoCount === 0 ? t("home.todo.none") : t("home.todo.title")}
          </h2>
          {todoCount === 0 ? null : (
            <ul className="research-home__list">
              {waiting.map((experiment) => (
                <li key={experiment.record.experiment_id}>
                  <Badge tone="info">{t("experiments.awaitingCandidate")}</Badge>
                  <span>
                    {tFill("home.todo.candidate", {
                      id: experiment.record.experiment_id.slice(0, 8),
                    })}
                  </span>
                  <Link
                    className="ui-button ui-button--secondary ui-button--small"
                    to="/research/experiments/$experimentId"
                    params={{ experimentId: experiment.record.experiment_id }}
                    hash="candidate"
                  >
                    {t("experiments.notice.candidates")}
                  </Link>
                </li>
              ))}
              {failures.map((summary) => (
                <li key={summary.run.run_id}>
                  <Badge tone="error">{t("history.strategies.failed")}</Badge>
                  <span className="research-home__failure">
                    {tFill("home.todo.failure", {
                      source: sourceLabel(summary),
                      stage: runStageLabel(summary.run.stage),
                    })}
                    <BacktestRunFailure run={summary.run} announce={false} />
                  </span>
                  <Link
                    className="ui-button ui-button--secondary ui-button--small"
                    to="/research/backtests/$runId"
                    params={{ runId: summary.run.run_id }}
                  >
                    {t("home.todo.failureOpen")}
                  </Link>
                </li>
              ))}
              {drafts.map((draft) => (
                <li key={draft.key}>
                  <Badge tone="warn">{t("home.todo.draft")}</Badge>
                  <span>
                    {draft.strategyId === null
                      ? t("home.todo.draftNew")
                      : tFill("home.todo.draftRevision", {
                          strategy: draft.strategyId,
                          revision: draft.baseRevision ?? 0,
                        })}{" "}
                    <small className="research-home__muted">{displayTime(draft.savedAt)}</small>
                  </span>
                  {draft.strategyId === null || draft.baseRevision === null ? (
                    <Link
                      className="ui-button ui-button--secondary ui-button--small"
                      to="/research/strategies/new"
                      search={{}}
                    >
                      {t("home.todo.draftOpen")}
                    </Link>
                  ) : (
                    <Link
                      className="ui-button ui-button--secondary ui-button--small"
                      to="/research/strategies/$strategyId/revisions/$revision"
                      params={{
                        strategyId: draft.strategyId,
                        revision: String(draft.baseRevision),
                      }}
                      search={{}}
                    >
                      {t("home.todo.draftOpen")}
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
        <AccountCard />
        <section className="research-home__card" aria-labelledby="home-running-title">
          <h2 id="home-running-title">{t("home.running.title")}</h2>
          {slots === undefined ? null : (
            <p className="research-home__muted">{tFill("experiments.slots", slots)}</p>
          )}
          {runningExperiments.length === 0 && runningBacktests.length === 0 ? (
            <p>{t("home.running.none")}</p>
          ) : (
            <ul className="research-home__list">
              {runningExperiments.map((experiment) => (
                <li key={experiment.record.experiment_id} className="research-home__progress">
                  <Link
                    to="/research/experiments/$experimentId"
                    params={{ experimentId: experiment.record.experiment_id }}
                  >
                    {tFill("home.activity.experiment", {
                      id: experiment.record.experiment_id.slice(0, 8),
                    })}
                  </Link>
                  <Badge tone={experimentStatusTone(experiment.status)}>
                    {experimentStatusLabel(experiment.status)}
                  </Badge>
                  <TrialProgress counts={experiment.trial_counts} />
                </li>
              ))}
              {runningBacktests.map((summary) => (
                <li key={summary.run.run_id} className="research-home__progress">
                  <Link to="/research/backtests/$runId" params={{ runId: summary.run.run_id }}>
                    {sourceLabel(summary)}
                  </Link>
                  <span>
                    {runStageLabel(summary.run.stage)} · {Math.round(summary.run.progress * 100)}%
                  </span>
                  <progress
                    className="research-home__bar"
                    value={summary.run.progress}
                    max={1}
                    aria-label={sourceLabel(summary)}
                  />
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="research-home__card research-home__wide" aria-labelledby="home-results-title">
          <h2 id="home-results-title">{t("home.results.title")}</h2>
          {results.length === 0 ? (
            <p>{t("home.results.none")}</p>
          ) : (
            <div className="data-list-page__scroll">
              <table className="data-list-page__table">
                <caption className="sr-only">{t("home.results.title")}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t("home.results.source")}</th>
                    <th scope="col">{t("home.results.finished")}</th>
                    <th scope="col">{t("history.strategies.result")}</th>
                    <th scope="col">{t("history.actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((summary) => (
                    <tr key={summary.run.run_id}>
                      <td>{sourceLabel(summary)}</td>
                      <td>{displayTime(summary.run.updated_at)}</td>
                      <td>
                        <RunHeadline headline={summary.headline} />
                      </td>
                      <td>
                        <Link
                          className="ui-button ui-button--ghost ui-button--small"
                          to="/research/backtests/$runId"
                          params={{ runId: summary.run.run_id }}
                        >
                          {t("home.results.open")}
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <section className="research-home__card research-home__wide" aria-labelledby="home-activity-title">
          <h2 id="home-activity-title">{t("home.activity.title")}</h2>
          <p className="research-home__muted">{t("home.activity.note")}</p>
          {activity.length === 0 ? (
            <p>{t("home.activity.none")}</p>
          ) : (
            <ol className="research-home__list">
              {activity.map((entry, index) => (
                <li key={`${entry.kind}:${entry.at}:${index}`}>
                  <small className="research-home__muted">{displayTime(entry.at)}</small>
                  <ActivityText entry={entry} />
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
    </section>
  );
};
