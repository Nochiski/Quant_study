import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";

import {
  backtestHistoryQuery,
  RUN_STATUS,
  RunHeadline,
  runStageLabel,
  trialLedgerQuery,
} from "../../../entities/backtest";
import { experimentsQuery, type Experiment } from "../../../entities/experiment";
import {
  strategiesQuery,
  strategyRevisionsQuery,
  type StrategySummary,
} from "../../../entities/strategy";
import { displayTime } from "../../../shared/lib/display-time";
import { t, tFill } from "../../../shared/config";
import { Link, useNavigate, useSearch } from "../../../shared/lib/router";
import {
  Badge,
  Button,
  EmptyState,
  panelId,
  tabId,
  Tabs,
} from "../../../shared/ui";
import "../../../shared/ui/data-list.css";
import { TrialLedgerPanel } from "./trial-ledger";
import "./trial-ledger.css";

const ROUTE = "/research/strategies";
const PAGE_SIZE = 20;
const REVISION_PAGE_SIZE = 20;

const lastPageOffset = (total: number, pageSize: number): number =>
  total === 0 ? 0 : Math.floor((total - 1) / pageSize) * pageSize;

const RevisionRows = ({
  strategyId,
  strategyLabel,
}: {
  strategyId: string;
  strategyLabel: string;
}) => {
  const [offset, setOffset] = useState(0);
  const revisions = useQuery(
    strategyRevisionsQuery(strategyId, {
      offset,
      limit: REVISION_PAGE_SIZE,
    }),
  );
  if (revisions.isPending) {
    return <p role="status">{t("history.revisions.loading")}</p>;
  }
  if (revisions.isError) {
    return (
      <div className="data-list-page__feedback" role="alert">
        <p>{t("history.revisions.error")}</p>
        <Button size="small" tone="ghost" onClick={() => revisions.refetch()}>
          {t("page.error.retry")}
        </Button>
      </div>
    );
  }
  if (revisions.data.total === 0) {
    return <p>{t("history.revisions.empty")}</p>;
  }
  return (
    <>
      <div className="data-list-page__scroll">
        <table className="data-list-page__table">
          <caption className="sr-only">
            {t("history.revisions.caption")}
          </caption>
          <thead>
            <tr>
              <th scope="col">{t("history.revisions.revision")}</th>
              <th scope="col">{t("history.revisions.created")}</th>
              <th scope="col">{t("history.revisions.source")}</th>
              <th scope="col">{t("history.revisions.specHash")}</th>
              <th scope="col">{t("history.revisions.note")}</th>
              <th scope="col">{t("history.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {revisions.data.items.map((revision) => (
              <tr key={revision.revision}>
                <td>
                  v{revision.revision}
                  {revision.requires_upgrade ? (
                    <>
                      {" "}
                      <Badge tone="warn">{t("history.frozen")}</Badge>
                    </>
                  ) : null}
                </td>
                <td>{displayTime(revision.created_at)}</td>
                <td>
                  {revision.source_format ?? revision.origin}
                  <br />
                  {revision.source_hash === null ? (
                    <span className="data-list-page__hash">
                      {t("history.revisions.sourceUnavailable")}
                    </span>
                  ) : (
                    <code
                      className="data-list-page__hash"
                      title={revision.source_hash}
                    >
                      {revision.source_hash.slice(0, 12)}
                    </code>
                  )}
                </td>
                <td>
                  <code
                    className="data-list-page__hash"
                    title={revision.spec_hash}
                  >
                    {revision.spec_hash.slice(0, 12)}
                  </code>
                </td>
                <td>{revision.change_note || "—"}</td>
                <td>
                  <span className="data-list-page__actions">
                    <Link
                      className="ui-button ui-button--secondary ui-button--small"
                      to="/research/strategies/$strategyId/revisions/$revision"
                      params={{
                        strategyId,
                        revision: String(revision.revision),
                      }}
                      search={{}}
                    >
                      {t("history.edit")}
                    </Link>
                    <Link
                      className="ui-button ui-button--ghost ui-button--small"
                      to="/research/strategies/$strategyId/revisions/$revision"
                      params={{
                        strategyId,
                        revision: String(revision.revision),
                      }}
                      search={{ view: "graph", compare: true }}
                    >
                      Diff
                    </Link>
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {revisions.data.total > REVISION_PAGE_SIZE ? (
        <nav
          className="data-list-page__pager"
          aria-label={`${t("history.revisions.pagination")}: ${strategyLabel}`}
        >
          <Button
            size="small"
            tone="ghost"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - REVISION_PAGE_SIZE))}
          >
            {t("history.previous")}
          </Button>
          <span>
            {offset + 1}–
            {Math.min(offset + REVISION_PAGE_SIZE, revisions.data.total)} /{" "}
            {revisions.data.total}
          </span>
          <Button
            size="small"
            tone="ghost"
            disabled={offset + REVISION_PAGE_SIZE >= revisions.data.total}
            onClick={() => setOffset(offset + REVISION_PAGE_SIZE)}
          >
            {t("history.next")}
          </Button>
        </nav>
      ) : null}
    </>
  );
};

type HistoryTab = "revisions" | "trials";

/** 펼친 전략 행: 리비전과 계열 시도 원장(검증 랩 V5-03)을 탭으로 오간다. */
const StrategyHistory = ({
  strategyId,
  strategyLabel,
}: {
  strategyId: string;
  strategyLabel: string;
}) => {
  const [tab, setTab] = useState<HistoryTab>("revisions");
  const tabsId = useId();
  const ledger = useQuery(trialLedgerQuery(strategyId));
  const trials = t("history.trials.tab");
  return (
    <>
      <Tabs
        items={[
          { id: "revisions", label: t("history.revisions.tab") },
          {
            id: "trials",
            label: ledger.isSuccess
              ? `${trials} ${ledger.data.trial_count}`
              : trials,
          },
        ]}
        value={tab}
        onChange={setTab}
        label={`${t("history.strategies.history")}: ${strategyLabel}`}
        idBase={tabsId}
      />
      <div
        role="tabpanel"
        id={panelId(tabsId, tab)}
        aria-labelledby={tabId(tabsId, tab)}
      >
        {tab === "revisions" ? (
          <RevisionRows strategyId={strategyId} strategyLabel={strategyLabel} />
        ) : (
          <TrialLedgerPanel
            strategyId={strategyId}
            strategyLabel={strategyLabel}
            ledger={ledger}
          />
        )}
      </div>
    </>
  );
};

/** 그 전략의 가장 최근 단일 백테스트 한 건. 숫자는 목록 응답의 `headline` 그대로다(결정 1). */
const LatestResult = ({ strategyId }: { strategyId: string }) => {
  const latest = useQuery(backtestHistoryQuery({ strategyId, kind: "single", limit: 1 }));
  const run = latest.data?.items[0];
  if (latest.isPending) return <span role="status">{t("page.loading")}</span>;
  if (latest.isError) return <span>{t("history.strategies.resultError")}</span>;
  if (run === undefined) return <span>{t("history.strategies.noRun")}</span>;
  if (run.run.status === "failed")
    return (
      <span className="data-list-page__actions">
        <Badge tone="error">{t("history.strategies.failed")}</Badge>
        <small>{tFill("history.session.failedAt", { stage: runStageLabel(run.run.stage) })}</small>
      </span>
    );
  if (RUN_STATUS[run.run.status].active)
    return <Badge tone="info">{t("history.strategies.running")}</Badge>;
  return <RunHeadline headline={run.headline} />;
};

const TrialCount = ({ strategyId }: { strategyId: string }) => {
  const ledger = useQuery(trialLedgerQuery(strategyId));
  return <>{ledger.isSuccess ? ledger.data.trial_count : "—"}</>;
};

const strategyExperiments = (experiments: readonly Experiment[], strategyId: string) =>
  experiments.filter((experiment) => {
    const source = experiment.record.run.strategy_source;
    return source?.kind === "saved_revision" && source.strategy_id === strategyId;
  });

/** 진행 중 작업: 끝나지 않은 실험 수와 후보 선택을 기다리는 실험 수. 실험 상태는 backend 가 파생했다. */
const ActiveWork = ({ experiments }: { experiments: readonly Experiment[] }) => {
  const running = experiments.filter((experiment) => !experiment.finished).length;
  const awaiting = experiments.filter(
    (experiment) => experiment.status === "completed" && experiment.selections.length === 0,
  ).length;
  if (running === 0 && awaiting === 0) return <>—</>;
  return (
    <span className="data-list-page__actions">
      {running > 0 ? (
        <Badge tone="info">{tFill("history.strategies.experimentsRunning", { count: running })}</Badge>
      ) : null}
      {awaiting > 0 ? (
        <Badge tone="info">{tFill("history.strategies.awaitingCandidate", { count: awaiting })}</Badge>
      ) : null}
    </span>
  );
};

const StrategyRow = ({
  strategy,
  experiments,
}: {
  strategy: StrategySummary;
  experiments: readonly Experiment[];
}) => {
  const [expanded, setExpanded] = useState(false);
  const historyId = useId();
  const strategyLabel = strategy.title
    ? `${strategy.title} (${strategy.strategy_id})`
    : strategy.strategy_id;
  const historyLabel = `${t("history.strategies.history")}: ${strategyLabel}`;
  return (
    <>
      <tr>
        <td>
          <span className="data-list-page__title">
            <Link
              to="/research/strategies/$strategyId"
              params={{ strategyId: strategy.strategy_id }}
            >
              <strong>{strategy.title || t("page.revision.untitled")}</strong>
            </Link>
            <code>{strategy.strategy_id}</code>
          </span>
        </td>
        <td>
          v{strategy.latest_revision}
          {strategy.requires_upgrade ? (
            <>
              {" "}
              <Badge tone="warn">{t("history.frozen")}</Badge>{" "}
              <Link
                className="ui-button ui-button--secondary ui-button--small"
                to="/research/strategies/$strategyId/revisions/$revision"
                params={{
                  strategyId: strategy.strategy_id,
                  revision: String(strategy.latest_revision),
                }}
                search={{}}
              >
                {t("history.session.upgrade")}
              </Link>
            </>
          ) : null}
        </td>
        <td>
          <LatestResult strategyId={strategy.strategy_id} />
        </td>
        <td>
          <TrialCount strategyId={strategy.strategy_id} />
        </td>
        <td>
          <ActiveWork experiments={experiments} />
        </td>
        <td>{displayTime(strategy.updated_at)}</td>
        <td>
          <span className="data-list-page__actions">
            <Link
              className="ui-button ui-button--primary ui-button--small"
              to="/research/strategies/$strategyId"
              params={{ strategyId: strategy.strategy_id }}
            >
              {t("history.strategies.openSession")}
            </Link>
            <Link
              className="ui-button ui-button--secondary ui-button--small"
              to="/research/strategies/$strategyId/revisions/$revision"
              params={{
                strategyId: strategy.strategy_id,
                revision: String(strategy.latest_revision),
              }}
              search={{}}
            >
              {t("history.openLatest")}
            </Link>
            <Button
              size="small"
              tone="ghost"
              aria-expanded={expanded}
              aria-controls={historyId}
              aria-label={`${
                expanded
                  ? t("history.revisions.close")
                  : t("history.revisions.open")
              }: ${strategyLabel}`}
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded
                ? t("history.revisions.close")
                : t("history.revisions.open")}
            </Button>
          </span>
        </td>
      </tr>
      {expanded ? (
        <tr>
          <td colSpan={7} className="data-list-page__nested">
            <div id={historyId} role="region" aria-label={historyLabel}>
              <StrategyHistory
                strategyId={strategy.strategy_id}
                strategyLabel={strategyLabel}
              />
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
};

export const StrategiesPage = () => {
  const search = useSearch({ from: ROUTE });
  const navigate = useNavigate();
  const offset = search.offset ?? 0;
  const strategies = useQuery(strategiesQuery({ offset, limit: PAGE_SIZE }));
  // 실험 목록은 한 번만 읽어 전략마다 나눈다. 첫 쪽(최근 실험)만 본다.
  const experiments = useInfiniteQuery(experimentsQuery());
  const experimentItems = experiments.data?.pages.flatMap((page) => page.items) ?? [];
  const correctedOffset =
    strategies.data !== undefined &&
    offset > 0 &&
    offset >= strategies.data.total
      ? lastPageOffset(strategies.data.total, PAGE_SIZE)
      : offset;

  useEffect(() => {
    if (correctedOffset === offset) return;
    void navigate({
      to: ROUTE,
      search: { offset: correctedOffset === 0 ? undefined : correctedOffset },
      replace: true,
    });
  }, [correctedOffset, navigate, offset]);

  const move = (next: number) =>
    void navigate({
      to: ROUTE,
      search: { offset: next === 0 ? undefined : next },
    });

  return (
    <section className="page data-list-page" aria-labelledby="strategies-title">
      <header className="data-list-page__header">
        <div>
          <h1 id="strategies-title">{t("history.strategies.title")}</h1>
          <p>{t("history.strategies.description")}</p>
        </div>
        <Link
          className="ui-button ui-button--primary"
          to="/research/strategies/new"
          search={{ guide: true }}
        >
          {t("history.strategies.new")}
        </Link>
      </header>
      {strategies.isPending || correctedOffset !== offset ? (
        <p className="page-state" role="status">
          {t("page.loading")}
        </p>
      ) : strategies.isError ? (
        <div className="page-state page-state--error" role="alert">
          <p>{t("history.strategies.error")}</p>
          <Button tone="ghost" onClick={() => strategies.refetch()}>
            {t("page.error.retry")}
          </Button>
        </div>
      ) : strategies.data.total === 0 ? (
        <EmptyState
          title={t("ui.emptyState.noStrategies")}
          description={t("history.strategies.empty")}
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
      ) : (
        <div className="data-list-page__panel">
          <details className="data-list-page__filter">
            <summary>{t("history.strategies.trialsHelp")}</summary>
            <p>{t("history.strategies.trialsHelpBody")}</p>
          </details>
          <div className="data-list-page__scroll">
            <table className="data-list-page__table">
              <caption className="sr-only">
                {t("history.strategies.caption")}
              </caption>
              <thead>
                <tr>
                  <th scope="col">{t("history.strategies.strategy")}</th>
                  <th scope="col">{t("history.strategies.latest")}</th>
                  <th scope="col">{t("history.strategies.result")}</th>
                  <th scope="col">{t("history.strategies.trials")}</th>
                  <th scope="col">{t("history.strategies.active")}</th>
                  <th scope="col">{t("history.strategies.updated")}</th>
                  <th scope="col">{t("history.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {strategies.data.items.map((strategy) => (
                  <StrategyRow
                    key={strategy.strategy_id}
                    strategy={strategy}
                    experiments={strategyExperiments(experimentItems, strategy.strategy_id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <nav
            className="data-list-page__pager"
            aria-label={t("history.strategies.pagination")}
          >
            <Button
              size="small"
              tone="ghost"
              disabled={offset === 0}
              onClick={() => move(Math.max(0, offset - PAGE_SIZE))}
            >
              {t("history.previous")}
            </Button>
            <span>
              {offset + 1}–{Math.min(offset + PAGE_SIZE, strategies.data.total)}{" "}
              / {strategies.data.total}
            </span>
            <Button
              size="small"
              tone="ghost"
              disabled={offset + PAGE_SIZE >= strategies.data.total}
              onClick={() => move(offset + PAGE_SIZE)}
            >
              {t("history.next")}
            </Button>
          </nav>
        </div>
      )}
    </section>
  );
};
