import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";

import {
  strategiesQuery,
  strategyRevisionsQuery,
  type StrategySummary,
} from "../../../entities/strategy";
import { t } from "../../../shared/config";
import { Link, useNavigate, useSearch } from "../../../shared/lib/router";
import { Button, EmptyState } from "../../../shared/ui";
import "../../../shared/ui/data-list.css";

const ROUTE = "/research/strategies";
const PAGE_SIZE = 20;
const REVISION_PAGE_SIZE = 20;

const displayTime = (value: string): string => {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? value
    : new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "UTC",
        timeZoneName: "short",
      }).format(parsed);
};

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
                <td>v{revision.revision}</td>
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
                      search={{ view: "diff" }}
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

const StrategyRow = ({ strategy }: { strategy: StrategySummary }) => {
  const [expanded, setExpanded] = useState(false);
  const historyId = useId();
  const strategyLabel = strategy.title
    ? `${strategy.title} (${strategy.strategy_id})`
    : strategy.strategy_id;
  const historyLabel = `${t("history.revisions.caption")}: ${strategyLabel}`;
  return (
    <>
      <tr>
        <td>
          <span className="data-list-page__title">
            <strong>{strategy.title || t("page.revision.untitled")}</strong>
            <code>{strategy.strategy_id}</code>
          </span>
        </td>
        <td>v{strategy.latest_revision}</td>
        <td>{displayTime(strategy.updated_at)}</td>
        <td>
          <code className="data-list-page__hash" title={strategy.spec_hash}>
            {strategy.spec_hash.slice(0, 12)}
          </code>
        </td>
        <td>
          <span className="data-list-page__actions">
            <Link
              className="ui-button ui-button--primary ui-button--small"
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
          <td colSpan={5} className="data-list-page__nested">
            <div id={historyId} role="region" aria-label={historyLabel}>
              <RevisionRows
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
    <section className="data-list-page" aria-labelledby="strategies-title">
      <header className="data-list-page__header">
        <div>
          <h1 id="strategies-title">{t("history.strategies.title")}</h1>
          <p>{t("history.strategies.description")}</p>
        </div>
        <Link
          className="ui-button ui-button--primary"
          to="/research/strategies/new"
          search={{}}
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
              search={{}}
            >
              {t("history.strategies.new")}
            </Link>
          }
        />
      ) : (
        <div className="data-list-page__panel">
          <div className="data-list-page__scroll">
            <table className="data-list-page__table">
              <caption className="sr-only">
                {t("history.strategies.caption")}
              </caption>
              <thead>
                <tr>
                  <th scope="col">{t("history.strategies.strategy")}</th>
                  <th scope="col">{t("history.strategies.latest")}</th>
                  <th scope="col">{t("history.strategies.updated")}</th>
                  <th scope="col">{t("history.strategies.hash")}</th>
                  <th scope="col">{t("history.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {strategies.data.items.map((strategy) => (
                  <StrategyRow key={strategy.strategy_id} strategy={strategy} />
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
