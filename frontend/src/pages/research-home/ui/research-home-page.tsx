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
import {
  strategiesQuery,
  strategyRevisionLabel,
  useStrategyTitles,
  type StrategyTitles,
} from "../../../entities/strategy";
import { listDrafts } from "../../../features/edit-strategy";
import { LedgerRefreshControl } from "../../../features/refresh-ledger";
import {
  daysBefore,
  marketOverviewQuery,
  sessionLabel,
} from "../../../entities/market";
import { t, tFill } from "../../../shared/config";
import { browserStorage } from "../../../shared/lib/browser-storage";
import { displayTime } from "../../../shared/lib/display-time";
import { Link, useNavigate, useSearch } from "../../../shared/lib/router";
import { Badge, EmptyState } from "../../../shared/ui";
import "../../../shared/ui/data-list.css";
import { MarketOverview } from "../../../widgets/market-overview";
import { activityLog, awaitingCandidate, type ActivityEntry } from "../model/home-model";
import "./research-home-page.css";

// 홈은 최근 것만 본다. 오래된 기록은 각 목록 화면이 소유한다.
const RECENT_RUNS = 20;
const FAILURES_SHOWN = 2;
const RESULTS_SHOWN = 5;
const ACTIVITY_SHOWN = 10;

const sourceLabel = (
  summary: BacktestRunSummary,
  titles: StrategyTitles,
): string => {
  const provenance = summary.strategy_provenance;
  return provenance.kind === "saved_revision" &&
    provenance.strategy_id &&
    provenance.revision
    ? strategyRevisionLabel(titles, provenance.strategy_id, provenance.revision)
    : t("history.backtests.unsaved");
};

const ActivityText = ({
  entry,
  titles,
}: {
  entry: ActivityEntry;
  titles: StrategyTitles;
}) => {
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
            { source: sourceLabel(entry.summary, titles) },
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
    <h3 id="home-account-title">
      {t("home.account.title")} <Badge tone="neutral">{t("home.account.unconnected")}</Badge>
    </h3>
    <p>{t("home.account.body")}</p>
  </section>
);

/** 제목과 시장 데이터 기준일·DB 새로고침. 기준일은 시장 블록과 같은 응답에서 읽는다. */
const HomeHeader = () => {
  const overview = useQuery(marketOverviewQuery());
  const session = overview.data?.status === "available" ? overview.data.session : null;
  const days = session ? daysBefore(session, new Date()) : null;
  return (
    <header className="research-home__header">
      <a className="research-home__skip" href="#home-research">
        {t("home.skipToResearch")}
      </a>
      <h1 id="home-title">{t("home.title")}</h1>
      <div className="research-home__data">
        {session ? (
          <span className="research-home__session">
            {days === 0
              ? tFill("market.sessionToday", { date: sessionLabel(session) })
              : tFill("market.session", { date: sessionLabel(session), days: days ?? "?" })}
          </span>
        ) : null}
        <LedgerRefreshControl />
      </div>
    </header>
  );
};

/** 시장보다 먼저 읽는 할 일 한 줄(US-DM-15). 누르면 아래 연구 카드로 간다. 할 일이 없으면 그리지 않는다. */
const TodoSummary = ({
  failed,
  awaiting,
  running,
}: {
  failed: number;
  awaiting: number;
  running: number;
}) => {
  if (failed + awaiting + running === 0) return null;
  const item = (tone: "error" | "info" | "neutral", badge: string, count: string) => (
    <a className="research-home__summary-item" href="#home-research">
      <Badge tone={tone}>{badge}</Badge>
      {count}
    </a>
  );
  return (
    <nav className="research-home__summary" aria-label={t("home.summary.label")}>
      {failed > 0
        ? item("error", t("home.summary.failed"), tFill("home.summary.failedCount", { count: failed }))
        : null}
      {awaiting > 0
        ? item(
            "info",
            t("home.summary.awaiting"),
            tFill("home.summary.awaitingCount", { count: awaiting }),
          )
        : null}
      {running > 0
        ? item(
            "neutral",
            t("home.summary.running"),
            tFill("home.summary.runningCount", { count: running }),
          )
        : null}
      <span className="research-home__muted">{t("home.summary.hint")}</span>
    </nav>
  );
};

/** 섹터맵 드릴다운 자리는 주소가 갖는다 — 뒤로 가기가 전체 지도로 돌아온다. */
const useSector = (): [string | undefined, (code: string | undefined) => void] => {
  const search = useSearch({ from: "/" });
  const navigate = useNavigate();
  return [
    search.sector,
    (code) => void navigate({ to: "/", search: code === undefined ? {} : { sector: code } }),
  ];
};

export const ResearchHomePage = () => {
  // 로컬 보관함은 React 밖 상태라 화면을 열 때 한 번 읽는다.
  const [drafts] = useState(() => listDrafts(browserStorage()));
  const strategies = useQuery(strategiesQuery({ limit: ACTIVITY_SHOWN }));
  const backtests = useQuery(backtestHistoryQuery({ kind: "single", limit: RECENT_RUNS }));
  const experiments = useInfiniteQuery(experimentsQuery());
  const experimentItems = experiments.data?.pages.flatMap((page) => page.items) ?? [];
  const slots = experiments.data?.pages[0]?.slots;
  const runs = backtests.data?.items ?? [];
  const titles = useStrategyTitles(
    runs.map((summary) => summary.strategy_provenance.strategy_id),
  );
  const [sector, setSector] = useSector();

  if (strategies.isSuccess && strategies.data.total === 0)
    return (
      <section className="page research-home" aria-labelledby="home-title">
        <HomeHeader />
        {/* 첫 방문의 주 행동은 하나뿐이다 — 시장보다 위에 둔다(US-DM-15). */}
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
        <MarketOverview sector={sector} onSector={setSector} />
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
      <HomeHeader />
      <TodoSummary
        failed={failures.length}
        awaiting={waiting.length}
        running={runningExperiments.length + runningBacktests.length}
      />
      <MarketOverview sector={sector} onSector={setSector} />
      <section
        id="home-research"
        className="research-home__research"
        tabIndex={-1}
        aria-labelledby="home-research-title"
      >
      <h2 id="home-research-title">{t("home.research.title")}</h2>
      <div className="research-home__grid">
        <section className="research-home__card research-home__todo" aria-labelledby="home-todo-title">
          <h3 id="home-todo-title">
            {todoCount === 0 ? t("home.todo.none") : t("home.todo.title")}
          </h3>
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
                      source: sourceLabel(summary, titles),
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
          <h3 id="home-running-title">{t("home.running.title")}</h3>
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
                    {sourceLabel(summary, titles)}
                  </Link>
                  <span>
                    {runStageLabel(summary.run.stage)} · {Math.round(summary.run.progress * 100)}%
                  </span>
                  <progress
                    className="research-home__bar"
                    value={summary.run.progress}
                    max={1}
                    aria-label={sourceLabel(summary, titles)}
                  />
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="research-home__card research-home__wide" aria-labelledby="home-results-title">
          <h3 id="home-results-title">{t("home.results.title")}</h3>
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
                      <td>{sourceLabel(summary, titles)}</td>
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
          <h3 id="home-activity-title">{t("home.activity.title")}</h3>
          <p className="research-home__muted">{t("home.activity.note")}</p>
          {activity.length === 0 ? (
            <p>{t("home.activity.none")}</p>
          ) : (
            <ol className="research-home__list">
              {activity.map((entry, index) => (
                <li key={`${entry.kind}:${entry.at}:${index}`}>
                  <small className="research-home__muted">{displayTime(entry.at)}</small>
                  <ActivityText entry={entry} titles={titles} />
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
      </section>
    </section>
  );
};
