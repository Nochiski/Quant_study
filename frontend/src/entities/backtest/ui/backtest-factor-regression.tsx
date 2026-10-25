import { t, tCode } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import { displayTime } from "../../../shared/lib/display-time";
import { useFactorRegression } from "../model/use-factor-regression";
import { BacktestResultFailure } from "./backtest-run-failure";

const number = (value: number | null) =>
  value === null ? "—" : value.toFixed(4);

/** 조회된 수치와 판본을 읽는다. 알파·t 기준·회귀를 화면에서 다시 계산하지 않는다. */
export const BacktestFactorRegression = ({
  runId,
  fingerprint,
}: {
  runId: string;
  fingerprint: string;
}) => {
  const query = useFactorRegression(runId, fingerprint);
  const data = query.error === null ? query.data : undefined;
  const report = data?.report;
  const fit = report?.fit;
  return (
    <section
      className="analysis-panel"
      aria-label={t("backtest.regression.title")}
    >
      <header>
        <h3>{t("backtest.regression.title")}</h3>
        {!query.gone ? (
          <Button disabled={query.isFetching} onClick={query.start}>
            {t(
              query.isFetching
                ? "backtest.regression.loading"
                : query.started
                  ? "backtest.regression.refresh"
                  : "backtest.regression.open",
            )}
          </Button>
        ) : null}
      </header>
      <p>{t("backtest.regression.meaning")}</p>
      {query.error !== null ? (
        <BacktestResultFailure error={query.error} />
      ) : null}
      {report ? (
        <>
          {report.unavailable_reason ? (
            <p role="status">
              {query.pollingStopped
                ? t("backtest.regression.pollingStopped")
                : tCode(
                    `backtest.regression.reason.${report.unavailable_reason}`,
                    report.unavailable_reason,
                  )}
            </p>
          ) : null}
          <p>
            {t("backtest.regression.samples")}: {report.observation_count} /{" "}
            {report.strategy_period_count}
          </p>
          <p>
            {t("backtest.regression.threshold")}:{" "}
            {number(data!.alpha_t_threshold)} · N {data!.trial_count ?? "—"}
          </p>
          <p>
            {t("backtest.regression.received")}:{" "}
            <time dateTime={new Date(query.dataUpdatedAt).toISOString()}>
              {displayTime(new Date(query.dataUpdatedAt).toISOString(), {
                seconds: true,
              })}
            </time>
          </p>
          {fit ? (
            <>
              <dl className="analysis-panel__facts">
                <div>
                  <dt>{t("backtest.regression.alpha")}</dt>
                  <dd>{(fit.alpha_annualized * 100).toFixed(2)}%</dd>
                </div>
                <div>
                  <dt>{t("backtest.regression.alphaT")}</dt>
                  <dd>{number(fit.alpha_t_value)}</dd>
                </div>
                <div>
                  <dt>{t("backtest.regression.rSquared")}</dt>
                  <dd>{number(fit.r_squared)}</dd>
                </div>
                <div>
                  <dt>{t("backtest.regression.hac")}</dt>
                  <dd>{fit.hac_lag}</dd>
                </div>
              </dl>
              <div
                role="region"
                aria-label={t("backtest.regression.exposures")}
                tabIndex={0}
                className="analysis-panel__scroll"
              >
                <table>
                  <caption>{t("backtest.regression.exposures")}</caption>
                  <thead>
                    <tr>
                      <th scope="col">{t("backtest.regression.factor")}</th>
                      <th scope="col">β</th>
                      <th scope="col">t</th>
                      <th scope="col">{t("backtest.regression.error")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fit.exposures.map((row) => (
                      <tr key={row.factor}>
                        <th scope="row">
                          {tCode(
                            `backtest.regression.factor.${row.factor}`,
                            row.factor,
                          )}
                        </th>
                        <td>{number(row.beta)}</td>
                        <td>{number(row.t_value)}</td>
                        <td>{number(row.standard_error)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
          <details className="ui-disclosure">
            <summary>{t("backtest.regression.basis")}</summary>
            <p>{t("backtest.regression.limitations")}</p>
            <dl className="analysis-panel__facts">
              <div>
                <dt>{t("backtest.regression.snapshot")}</dt>
                <dd>{data!.data_snapshot_id}</dd>
              </div>
              <div>
                <dt>{t("backtest.regression.registry")}</dt>
                <dd>{data!.factor_registry_version}</dd>
              </div>
              <div>
                <dt>{t("backtest.regression.evaluator")}</dt>
                <dd>{data!.factor_evaluation_version ?? "—"}</dd>
              </div>
              <div>
                <dt>{t("backtest.regression.days")}</dt>
                <dd>{report.annualization_days}</dd>
              </div>
              <div>
                <dt>{t("backtest.regression.sessionAlpha")}</dt>
                <dd>{fit?.alpha_per_session ?? "—"}</dd>
              </div>
              <div>
                <dt>{t("backtest.regression.alphaError")}</dt>
                <dd>{fit?.alpha_standard_error ?? "—"}</dd>
              </div>
            </dl>
            <ul>
              {report.excluded_periods.map((item) => (
                <li key={item.reason}>
                  {tCode(`backtest.regression.gap.${item.reason}`, item.reason)}
                  : {item.count}
                </li>
              ))}
            </ul>
          </details>
        </>
      ) : null}
    </section>
  );
};
