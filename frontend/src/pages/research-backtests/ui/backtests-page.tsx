import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, type FormEvent } from "react";

import {
  backtestHistoryQuery,
  type BacktestRunSummary,
} from "../../../entities/backtest";
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

const lastPageOffset = (total: number): number =>
  total === 0 ? 0 : Math.floor((total - 1) / PAGE_SIZE) * PAGE_SIZE;

const ShortHash = ({ value }: { value: string }) => (
  <code className="data-list-page__hash" title={value}>
    {value.slice(0, 12)}
  </code>
);

const StrategyFilter = ({
  initial,
  onApply,
}: {
  initial: string;
  onApply: (strategyId: string | undefined) => void;
}) => {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (inputRef.current !== null && inputRef.current.value !== initial) {
      inputRef.current.value = initial;
    }
  }, [initial]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onApply(inputRef.current?.value.trim() || undefined);
  };

  return (
    <form className="data-list-page__filter" onSubmit={submit}>
      <label htmlFor={inputId}>{t("history.backtests.filter")}</label>
      <input
        id={inputId}
        ref={inputRef}
        defaultValue={initial}
        autoComplete="off"
        placeholder={t("history.backtests.filterPlaceholder")}
      />
      <Button size="small" tone="secondary" type="submit">
        {t("history.backtests.applyFilter")}
      </Button>
    </form>
  );
};

const StrategySource = ({ summary }: { summary: BacktestRunSummary }) => {
  const provenance = summary.strategy_provenance;
  return (
    <>
      <strong>
        {provenance.kind === "saved_revision"
          ? t("history.backtests.saved")
          : t("history.backtests.inline")}
      </strong>
      <br />
      {provenance.strategy_id !== null &&
      provenance.strategy_id !== undefined &&
      provenance.revision !== null &&
      provenance.revision !== undefined ? (
        <Link
          to="/research/strategies/$strategyId/revisions/$revision"
          params={{
            strategyId: provenance.strategy_id,
            revision: String(provenance.revision),
          }}
          search={{}}
        >
          {provenance.strategy_id} · v{provenance.revision}
        </Link>
      ) : (
        <span>{t("history.backtests.unsaved")}</span>
      )}
    </>
  );
};

const Provenance = ({ summary }: { summary: BacktestRunSummary }) => {
  const provenance = summary.strategy_provenance;
  return (
    <dl className="data-list-page__provenance">
      <dt>{t("history.backtests.specHash")}</dt>
      <dd>
        <ShortHash value={provenance.spec_hash} />
      </dd>
      <dt>{t("history.backtests.schema")}</dt>
      <dd>{provenance.schema_version}</dd>
      <dt>{t("history.backtests.sourceHash")}</dt>
      <dd>
        {provenance.source_hash === null ||
        provenance.source_hash === undefined ? (
          t("history.backtests.sourceUnavailable")
        ) : (
          <ShortHash value={provenance.source_hash} />
        )}
      </dd>
    </dl>
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
  const correctedOffset =
    history.data !== undefined && offset > 0 && offset >= history.data.total
      ? lastPageOffset(history.data.total)
      : offset;

  useEffect(() => {
    if (correctedOffset === offset) return;
    void navigate({
      to: ROUTE,
      search: {
        strategy: search.strategy,
        offset: correctedOffset === 0 ? undefined : correctedOffset,
      },
      replace: true,
    });
  }, [correctedOffset, navigate, offset, search.strategy]);

  const move = (next: number) =>
    void navigate({
      to: ROUTE,
      search: {
        strategy: search.strategy,
        offset: next === 0 ? undefined : next,
      },
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
          initial={search.strategy ?? ""}
          onApply={(strategy) =>
            void navigate({
              to: ROUTE,
              search: { strategy, offset: undefined },
            })
          }
        />
        {history.isPending || correctedOffset !== offset ? (
          <p className="page-state" role="status">
            {t("page.loading")}
          </p>
        ) : history.isError ? (
          <div className="page-state page-state--error" role="alert">
            <p>{t("history.backtests.error")}</p>
            <Button tone="ghost" onClick={() => history.refetch()}>
              {t("page.error.retry")}
            </Button>
          </div>
        ) : history.data.total === 0 ? (
          <EmptyState
            title={t("history.backtests.emptyTitle")}
            description={
              search.strategy
                ? t("history.backtests.filteredEmpty")
                : t("history.backtests.empty")
            }
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
                    <th scope="col">{t("history.backtests.run")}</th>
                    <th scope="col">{t("history.backtests.status")}</th>
                    <th scope="col">{t("history.backtests.strategy")}</th>
                    <th scope="col">{t("history.backtests.provenance")}</th>
                    <th scope="col">{t("history.backtests.time")}</th>
                    <th scope="col">{t("history.actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {history.data.items.map((item) => (
                    <tr key={item.run.run_id}>
                      <td>
                        <code>{item.run.run_id}</code>
                        <br />
                        <span className="data-list-page__hash">
                          {item.run.stage}
                        </span>
                      </td>
                      <td>
                        <Badge tone={TONE[item.run.status]}>
                          {item.run.status}
                        </Badge>
                        <br />
                        {Math.round(item.run.progress * 100)}%
                        {item.run.error ? (
                          <span className="data-list-page__error">
                            {item.run.error}
                          </span>
                        ) : null}
                      </td>
                      <td>
                        <StrategySource summary={item} />
                      </td>
                      <td>
                        <Provenance summary={item} />
                      </td>
                      <td>
                        {displayTime(item.run.created_at)}
                        <br />
                        <span className="data-list-page__hash">
                          {t("history.backtests.updated")} ·{" "}
                          {displayTime(item.run.updated_at)}
                        </span>
                      </td>
                      <td>
                        <Link
                          className="ui-button ui-button--secondary ui-button--small"
                          to="/research/backtests/$runId"
                          params={{ runId: item.run.run_id }}
                        >
                          {t("history.backtests.open")}
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <nav
              className="data-list-page__pager"
              aria-label={t("history.backtests.pagination")}
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
            </nav>
          </>
        )}
      </div>
    </section>
  );
};
