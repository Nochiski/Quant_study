import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import {
  continuousAccountErrorKey,
  continuousAccountQuery,
  continuousLatestQuery,
  useRunContinuousAccount,
} from "../../../entities/experiment";
import { BacktestMetricTable } from "../../../entities/backtest";
import {
  ApiRequestError,
  failureReason,
  type ContinuousAccountState,
} from "../../../shared/api";
import { t, tFill, type MessageKey } from "../../../shared/config";
import { Badge, Button, FailureNotice } from "../../../shared/ui";

const money = (value: number) =>
  `₩${value.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}`;
const percent = new Intl.NumberFormat("ko-KR", {
  style: "percent",
  maximumFractionDigits: 2,
  minimumFractionDigits: 2,
});

const STAGES: Record<ContinuousAccountState["stage"], MessageKey> = {
  preparing: "experiments.continuous.preparing",
  pipeline: "experiments.continuous.pipeline",
  compiling: "experiments.continuous.compiling",
  engine: "experiments.continuous.engine",
  completed: "experiments.continuous.completed",
  failed: "experiments.continuous.failedLabel",
  interrupted: "experiments.continuous.interruptedLabel",
};

const permanent = (error: unknown) =>
  error instanceof ApiRequestError && error.status >= 400 && error.status < 500;
const storageKey = (experimentId: string) =>
  `continuous-account-request:${experimentId}`;

/** 선택한 요청 ID만 보존한다. 서버 상태·결과의 owner는 query cache다. */
const rememberedRequest = (experimentId: string) => {
  try {
    return sessionStorage.getItem(storageKey(experimentId));
  } catch {
    return null;
  }
};

export const ContinuousAccount = ({
  experimentId,
}: {
  experimentId: string;
}) => {
  const [selection, setSelection] = useState(() => ({
    experimentId,
    requestId: rememberedRequest(experimentId),
  }));
  // 라우터가 같은 화면을 재사용할 때 다른 실험의 요청을 잠깐 표시하지 않는다.
  const requestId =
    selection.experimentId === experimentId
      ? selection.requestId
      : rememberedRequest(experimentId);
  const start = useRunContinuousAccount(experimentId);
  const requestPending = start.isPending && start.variables === requestId;
  const latest = useQuery(
    continuousLatestQuery(experimentId, requestId === null),
  );
  const detail = useQuery({
    ...continuousAccountQuery(requestId),
    refetchInterval: (query) => {
      if (
        query.state.error &&
        !(
          requestPending &&
          query.state.error instanceof ApiRequestError &&
          query.state.error.status === 404
        )
      )
        return false;
      return requestPending || query.state.data?.status === "running"
        ? 1_000
        : false;
    },
  });
  const state = requestId === null ? latest.data?.evaluation : detail.data;
  const unknown =
    requestId !== null &&
    detail.error instanceof ApiRequestError &&
    detail.error.status === 404;
  const lookupError =
    requestId === null ? latest.error : unknown ? null : detail.error;
  const running =
    requestPending || (state?.status === "running" && !lookupError);
  const unresolved = requestId !== null && state === undefined;
  const readiness = latest.data?.readiness;
  const canStart =
    readiness?.ready === true &&
    !running &&
    !unresolved &&
    !lookupError &&
    !latest.isError;
  const run = (id: string) => start.mutate(id);
  const begin = () => {
    const id = crypto.randomUUID();
    try {
      sessionStorage.setItem(storageKey(experimentId), id);
    } catch {
      /* 저장소를 쓸 수 없어도 현재 화면의 요청 ID를 유지한다. */
    }
    setSelection({ experimentId, requestId: id });
    run(id);
  };
  const check = () => {
    void latest.refetch();
    if (requestId !== null) void detail.refetch();
  };
  const result = state?.result;
  const summary = result?.summary;
  const interpretation =
    result?.interpretation ??
    latest.data?.interpretation ??
    t("experiments.continuous.assumption");
  const stage = state ? STAGES[state.stage] : undefined;
  return (
    <section
      className="experiment-section"
      aria-labelledby="continuous-account-title"
    >
      <div className="experiment-continuous__actions">
        <h2 id="continuous-account-title">
          {t("experiments.continuous.title")}
        </h2>
        <Badge>{t("experiments.continuous.mockLabel")}</Badge>
      </div>
      <p>{t("experiments.continuous.shortAssumption")}</p>
      {latest.isPending && requestId === null ? (
        <p role="status">{t("page.loading")}</p>
      ) : null}
      {lookupError ? (
        <FailureNotice
          message={t(
            permanent(lookupError)
              ? "experiments.continuous.permanent"
              : "experiments.continuous.loadFailed",
          )}
          reason={failureReason(lookupError)}
        >
          {!permanent(lookupError) ? (
            <Button onClick={check}>
              {t("experiments.continuous.reload")}
            </Button>
          ) : null}
        </FailureNotice>
      ) : null}
      {latest.isError && requestId !== null && !lookupError ? (
        <FailureNotice
          message={t("experiments.continuous.loadFailed")}
          reason={failureReason(latest.error)}
        >
          {!permanent(latest.error) ? (
            <Button onClick={() => void latest.refetch()}>
              {t("experiments.continuous.reload")}
            </Button>
          ) : null}
        </FailureNotice>
      ) : null}
      {start.isError && !state && start.variables === requestId ? (
        <FailureNotice
          message={t(
            (start.error instanceof ApiRequestError
              ? continuousAccountErrorKey(start.error.code)
              : undefined) ?? "experiments.continuous.startFailed",
          )}
          reason={failureReason(start.error)}
        />
      ) : null}
      {running ? (
        <div role="status">
          <p>{t(stage ?? "experiments.continuous.running")}</p>
          {state ? (
            <progress
              aria-label={t("experiments.continuous.running")}
              max={1}
              value={state.progress}
            />
          ) : null}
          {state ? (
            <p>
              {tFill("experiments.continuous.progress", {
                progress: (state.progress * 100).toFixed(0),
              })}
            </p>
          ) : null}
        </div>
      ) : null}
      {unresolved && !running && !lookupError ? (
        <div>
          <p role="status">{t("experiments.continuous.unconfirmed")}</p>
          <div className="experiment-continuous__actions">
            {!lookupError || !permanent(lookupError) ? (
              <Button onClick={check} disabled={detail.isFetching}>
                {t("experiments.continuous.check")}
              </Button>
            ) : null}
            {unknown && requestId !== null ? (
              <Button onClick={() => run(requestId)} disabled={requestPending}>
                {t("experiments.continuous.resend")}
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}
      {state?.status === "failed" || state?.status === "interrupted" ? (
        <FailureNotice
          message={t(
            state.status === "interrupted"
              ? "experiments.continuous.interrupted"
              : "experiments.continuous.failed",
          )}
          reason={state.error_detail ?? null}
        />
      ) : null}
      {!readiness?.ready && readiness ? (
        <FailureNotice
          announce={false}
          message={t(
            continuousAccountErrorKey(readiness.code) ??
              "experiments.continuous.blocked",
          )}
          reason={readiness.detail || null}
        />
      ) : null}
      {state?.status === "completed" && result && summary ? (
        <>
          <p role="status">{t("experiments.continuous.completed")}</p>
          <p>
            {tFill("experiments.continuous.recordPeriod", {
              first: summary.first_snapshot_date,
              last: summary.last_snapshot_date,
              count: summary.snapshot_count,
            })}
          </p>
          <dl className="experiment-continuous__summary">
            <div>
              <dt>{t("experiments.continuous.initialCash")}</dt>
              <dd>{money(summary.initial_cash)}</dd>
            </div>
            <div>
              <dt>{t("experiments.continuous.finalEquity")}</dt>
              <dd>{money(summary.final_equity)}</dd>
            </div>
            <div>
              <dt>{t("experiments.continuous.profit")}</dt>
              <dd>{money(summary.pnl)}</dd>
            </div>
            <div>
              <dt>{t("experiments.continuous.return")}</dt>
              <dd>{percent.format(summary.total_return)}</dd>
            </div>
          </dl>
          <dl className="experiment-continuous__summary">
            <div>
              <dt>{t("experiments.continuous.finalCash")}</dt>
              <dd>{money(summary.final_cash)}</dd>
            </div>
            <div>
              <dt>{t("experiments.continuous.fees")}</dt>
              <dd>{money(result.total_fees)}</dd>
            </div>
            <div>
              <dt>{t("experiments.continuous.tax")}</dt>
              <dd>{money(result.total_sell_tax)}</dd>
            </div>
          </dl>
          <p>{t("experiments.continuous.notLiquidated")}</p>
        </>
      ) : null}
      {(!unresolved || running) && !lookupError ? (
        <div className="experiment-continuous__actions">
          <Button tone="primary" onClick={begin} disabled={!canStart}>
            {t(
              running
                ? "experiments.continuous.starting"
                : state?.status === "completed"
                  ? "experiments.continuous.again"
                  : "experiments.continuous.start",
            )}
          </Button>
          {!state && !running ? (
            <Badge>{t("experiments.continuous.notStarted")}</Badge>
          ) : null}
        </div>
      ) : null}
      {state || requestId ? (
        <p className="experiment-continuous__identity">
          {t("experiments.continuous.id")}:{" "}
          <code>{state?.evaluation_id ?? requestId}</code>
        </p>
      ) : null}
      <details className="experiment-continuous__details ui-disclosure">
        <summary>{t("experiments.continuous.interpretation")}</summary>
        <p>{t("experiments.continuous.description")}</p>
        <p>{interpretation}</p>
        <p>{t("experiments.continuous.mock")}</p>
      </details>
      {state?.status === "completed" && result ? (
        <>
          <details className="experiment-continuous__details ui-disclosure">
            <summary>{t("experiments.continuous.metrics")}</summary>
            <p>{t("experiments.continuous.metricBasis")}</p>
            <div className="run-detail">
              <BacktestMetricTable
                definitions={result.metric_definitions}
                metrics={result.metrics}
              />
            </div>
          </details>
          <details className="experiment-continuous__details ui-disclosure">
            <summary>
              {tFill("experiments.continuous.fills", {
                count: result.artifacts.fills.length,
              })}
            </summary>
            <div
              className="data-list-page__scroll experiment-table-scroll"
              role="region"
              tabIndex={0}
              aria-label={t("experiments.continuous.fillRecords")}
            >
              <table className="data-list-page__table">
                <caption className="sr-only">
                  {t("experiments.continuous.fillRecords")}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">{t("backtest.orderEvents.session")}</th>
                    <th scope="col">{t("backtest.result.column.security")}</th>
                    <th scope="col">{t("backtest.result.column.side")}</th>
                    <th scope="col">{t("backtest.result.column.quantity")}</th>
                    <th scope="col">{t("experiments.continuous.fillPrice")}</th>
                    <th scope="col">{t("backtest.result.column.fees")}</th>
                    <th scope="col">{t("experiments.continuous.tax")}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.artifacts.fills.map((fill) => (
                    <tr key={fill.fill_id}>
                      <td>{fill.session}</td>
                      <td>
                        <code>{fill.security_id}</code>
                      </td>
                      <td>
                        {fill.side === "buy"
                          ? t("experiments.continuous.buy")
                          : fill.side === "sell"
                            ? t("experiments.continuous.sell")
                            : fill.side}
                      </td>
                      <td>{fill.quantity}</td>
                      <td>{money(fill.price)}</td>
                      <td>{money(fill.fee)}</td>
                      <td>
                        {fill.sell_tax == null ? "—" : money(fill.sell_tax)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      ) : null}
      {state?.status === "completed" && result ? (
        <details className="experiment-continuous__details ui-disclosure">
          <summary>{t("experiments.continuous.details")}</summary>
          <dl>
            {Object.entries(result.manifest).map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>
                  {typeof value === "object"
                    ? JSON.stringify(value)
                    : String(value ?? "—")}
                </dd>
              </div>
            ))}
          </dl>
          <ul>
            {result.warnings.map((warning, index) => (
              <li key={`${warning.code}-${index}`}>{warning.message}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
};
