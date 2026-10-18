import { useQuery } from "@tanstack/react-query";
import { LineChart } from "../../../entities/backtest";
import {
  capacityReportQuery,
  trialStatusLabel,
} from "../../../entities/experiment";
import { ApiRequestError, failureReason } from "../../../shared/api";
import { t, tCode } from "../../../shared/config";
import { Button, FailureNotice } from "../../../shared/ui";

const amount = (value: number | null) =>
  value === null ? "—" : value.toLocaleString();
const fixed = (value: number | null) =>
  value === null ? "—" : value.toFixed(4);
const percent = (value: number | null) =>
  value === null ? "—" : `${(value * 100).toFixed(2)}%`;

/** 상태·비용·한계 금액은 backend report 그대로다. 실패 금액에 샤프0을 넣지 않는다. */
export const CapacityReport = ({
  experimentId,
  settled,
}: {
  experimentId: string;
  settled: boolean;
}) => {
  const query = useQuery({
    ...capacityReportQuery(experimentId),
    refetchInterval: (current) =>
      current.state.error === null &&
      (!settled ||
        current.state.data?.points.some(
          (point) => point.status === "queued" || point.status === "running",
        ))
        ? 2_000
        : false,
  });
  const gone =
    query.error instanceof ApiRequestError && query.error.status === 410;
  return (
    <section
      className="analysis-panel"
      aria-label={t("backtest.capacity.results")}
    >
      <header>
        <h3>{t("backtest.capacity.results")}</h3>
        {!gone ? (
          <Button
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            {t("backtest.capacity.refresh")}
          </Button>
        ) : null}
      </header>
      {query.isPending ? (
        <p role="status">{t("backtest.capacity.loading")}</p>
      ) : null}
      {query.isError ? (
        <FailureNotice
          message={t("backtest.capacity.failed")}
          reason={failureReason(query.error)}
        />
      ) : null}
      {query.data && !query.isError ? (
        <>
          <p>
            {t("backtest.capacity.limit")}: {amount(query.data.limit.amount)}{" "}
            KRW
          </p>
          {query.data.limit.gap ? (
            <p>
              {tCode(
                `backtest.capacity.gap.${query.data.limit.gap}`,
                query.data.limit.gap,
              )}
            </p>
          ) : null}
          <p>
            {t("backtest.capacity.best")}:{" "}
            {amount(query.data.limit.best_amount)} KRW ·{" "}
            {t("backtest.capacity.threshold")}:{" "}
            {fixed(query.data.limit.threshold_sharpe)}
          </p>
          <LineChart
            title={t("backtest.capacity.curve")}
            series={[
              {
                label: t("backtest.capacity.sharpe"),
                color: "var(--accent)",
                values: query.data.points.map((point) => point.sharpe),
              },
            ]}
          />
          <p>{t("backtest.capacity.interpretation")}</p>
          <ol
            className="analysis-panel__sequence"
            aria-label={t("backtest.capacity.order")}
          >
            {query.data.points.map((point) => (
              <li key={point.trial_index}>{amount(point.initial_cash)} KRW</li>
            ))}
          </ol>
          <div
            role="region"
            aria-label={t("backtest.capacity.table")}
            tabIndex={0}
            className="analysis-panel__scroll"
          >
            <table>
              <caption>{t("backtest.capacity.table")}</caption>
              <thead>
                <tr>
                  <th scope="col">{t("backtest.capacity.orderColumn")}</th>
                  {(
                    [
                      "amount",
                      "status",
                      "sharpe",
                      "participation",
                      "impact",
                      "rounding",
                      "unfilled",
                    ] as const
                  ).map((key) => (
                    <th key={key} scope="col">
                      {t(`backtest.capacity.${key}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {query.data.points.map((point, index) => (
                  <tr key={point.trial_index}>
                    <td>{index + 1}</td>
                    <th scope="row">{amount(point.initial_cash)}</th>
                    <td>{trialStatusLabel(point.status)}</td>
                    <td>{fixed(point.sharpe)}</td>
                    <td>{percent(point.participation_rate)}</td>
                    <td>{fixed(point.impact_cost_bps)}</td>
                    <td>{percent(point.rounding_error)}</td>
                    <td>{percent(point.session_unfilled_ratio)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p>{t("backtest.capacity.nullMeaning")}</p>
          <p>{t("backtest.capacity.unfilledMeaning")}</p>
        </>
      ) : null}
    </section>
  );
};
