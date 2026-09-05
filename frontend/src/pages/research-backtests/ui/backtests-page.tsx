import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { backtestHistoryQuery } from "../../../entities/backtest";
import { t } from "../../../shared/config";
import { Link, useNavigate, useSearch } from "../../../shared/lib/router";
import { Badge, Button, EmptyState } from "../../../shared/ui";
import "../../../shared/ui/data-list.css";

const ROUTE = "/research/backtests";
const PAGE_SIZE = 25;
const TONE = {
  queued: "info",
  running: "info",
  cancel_requested: "warn",
  cancelled: "warn",
  failed: "error",
  completed: "ok",
} as const;

const displayTime = (value: string): string =>
  value.length >= 16
    ? `${value.slice(0, 10)} ${value.slice(11, 16)} UTC`
    : value;

const StrategyFilter = ({
  initial,
  onApply,
}: {
  initial: string;
  onApply: (strategy: string | undefined) => void;
}) => {
  const [filter, setFilter] = useState(initial);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onApply(filter.trim() || undefined);
  };
  return (
    <form className="data-list-page__filter" onSubmit={submit}>
      <label htmlFor="backtest-strategy-filter">
        {t("history.backtests.filter")}
      </label>
      <input
        id="backtest-strategy-filter"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
        placeholder={t("history.backtests.filterPlaceholder")}
      />
      <Button size="small" tone="secondary" type="submit">
        {t("history.backtests.applyFilter")}
      </Button>
    </form>
  );
};

export const BacktestsPage = () => {
  const search = useSearch({ from: ROUTE });
  const navigate = useNavigate();
  const offset = search.offset ?? 0;
  const history = useQuery(
    backtestHistoryQuery({
      offset,
      limit: PAGE_SIZE,
      strategyId: search.strategy,
    }),
  );
  const move = (next: number) =>
    void navigate({
      to: ROUTE,
      search: { ...search, offset: next === 0 ? undefined : next },
    });

  return (
    <section className="data-list-page" aria-labelledby="backtests-title">
      <header className="data-list-page__header">
        <div>
          <h1 id="backtests-title">{t("history.backtests.title")}</h1>
          <p>{t("history.backtests.description")}</p>
        </div>
      </header>
      <div className="data-list-page__panel">
        <StrategyFilter
          key={search.strategy ?? ""}
          initial={search.strategy ?? ""}
          onApply={(strategy) =>
            void navigate({
              to: ROUTE,
              search: { strategy, offset: undefined },
            })
          }
        />
        {history.isPending ? (
          <p className="page-state" role="status">
            {t("page.loading")}
          </p>
        ) : history.isError ? (
          <p className="page-state page-state--error" role="alert">
            {t("history.backtests.error")}
          </p>
        ) : history.data.total === 0 ? (
          <EmptyState
            title={t("history.backtests.emptyTitle")}
            description={t("history.backtests.empty")}
          />
        ) : (
          <>
            <div className="data-list-page__scroll">
              <table className="data-list-page__table">
                <caption className="sr-only">
                  {t("history.backtests.caption")}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Run</th>
                    <th scope="col">{t("history.backtests.status")}</th>
                    <th scope="col">{t("history.backtests.strategy")}</th>
                    <th scope="col">{t("history.backtests.created")}</th>
                    <th scope="col">{t("history.actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {history.data.items.map(
                    ({ run, strategy_provenance: source }) => (
                      <tr key={run.run_id}>
                        <td>
                          <code>{run.run_id}</code>
                          <br />
                          <span className="data-list-page__hash">
                            {run.stage}
                          </span>
                        </td>
                        <td>
                          <Badge tone={TONE[run.status]}>{run.status}</Badge>
                          <br />
                          {Math.round(run.progress * 100)}%
                        </td>
                        <td>
                          <strong>{source.kind}</strong>
                          <br />
                          {source.strategy_id && source.revision ? (
                            <Link
                              to="/research/strategies/$strategyId/revisions/$revision"
                              params={{
                                strategyId: source.strategy_id,
                                revision: String(source.revision),
                              }}
                              search={{}}
                            >
                              {source.strategy_id} · v{source.revision}
                            </Link>
                          ) : (
                            <span>{t("history.backtests.inline")}</span>
                          )}
                          <br />
                          <code
                            className="data-list-page__hash"
                            title={source.spec_hash}
                          >
                            {source.spec_hash.slice(0, 12)}
                          </code>
                        </td>
                        <td>{displayTime(run.created_at)}</td>
                        <td>
                          <Link
                            className="ui-button ui-button--secondary ui-button--small"
                            to="/research/backtests/$runId"
                            params={{ runId: run.run_id }}
                          >
                            {t("history.backtests.open")}
                          </Link>
                        </td>
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
            </div>
            <div className="data-list-page__pager">
              <Button
                size="small"
                tone="ghost"
                disabled={offset === 0}
                onClick={() => move(Math.max(0, offset - PAGE_SIZE))}
              >
                {t("history.previous")}
              </Button>
              <span>
                {offset + 1}–{Math.min(offset + PAGE_SIZE, history.data.total)}{" "}
                / {history.data.total}
              </span>
              <Button
                size="small"
                tone="ghost"
                disabled={offset + PAGE_SIZE >= history.data.total}
                onClick={() => move(offset + PAGE_SIZE)}
              >
                {t("history.next")}
              </Button>
            </div>
          </>
        )}
      </div>
    </section>
  );
};
