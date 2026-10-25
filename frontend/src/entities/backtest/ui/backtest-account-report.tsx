import { ApiRequestError, type AccountReport } from "../../../shared/api";
import { t, tCode } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import { BacktestResultFailure } from "./backtest-run-failure";
import "./backtest-account-report.css";

/** 금액의 계산·판정은 서버가 소유하고 화면은 단위와 자릿수만 표시한다. */
const percentage = new Intl.NumberFormat("ko-KR", {
  style: "percent",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const money = (value: number) =>
  `₩${value.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}`;

export const BacktestAccountReport = ({
  report,
  error,
  loading,
  retry,
}: {
  report: AccountReport | undefined;
  error: unknown;
  loading: boolean;
  retry: () => void;
}) => {
  const summary = report?.summary;
  const canRetry =
    error !== null &&
    (!(error instanceof ApiRequestError) ||
      error.status === 0 ||
      error.status >= 500);
  return (
    <div className="account-report">
      <h4>{t("backtest.account.title")}</h4>
      {loading ? <p role="status">{t("backtest.account.loading")}</p> : null}
      {error !== null ? (
        <div>
          <p>{t("backtest.account.failed")}</p>
          <BacktestResultFailure error={error} />
          {canRetry ? (
            <Button onClick={retry}>{t("backtest.account.retry")}</Button>
          ) : null}
        </div>
      ) : null}
      {report ? (
        <>
          <p>{report.interpretation}</p>
          <p className="account-report__basis">
            {t("backtest.account.basisNote")}
          </p>
          <p>
            {tCode(`backtest.account.status.${report.status}`, report.status)}
          </p>
          {report.detail ? <p>{report.detail}</p> : null}
          {summary ? (
            <>
              <dl className="account-report__highlights">
                <div>
                  <dt>{t("backtest.account.initialCash")}</dt>
                  <dd>{money(summary.initial_cash)}</dd>
                </div>
                <div>
                  <dt>{t("backtest.account.finalEquity")}</dt>
                  <dd>{money(summary.final_equity)}</dd>
                </div>
                <div>
                  <dt>{t("backtest.account.pnl")}</dt>
                  <dd>{money(summary.pnl)}</dd>
                </div>
                <div>
                  <dt>{t("backtest.account.totalReturn")}</dt>
                  <dd>{percentage.format(summary.total_return)}</dd>
                </div>
              </dl>
              <dl className="account-report__records">
                <div>
                  <dt>{t("backtest.account.finalCash")}</dt>
                  <dd>{money(summary.final_cash)}</dd>
                </div>
                <div>
                  <dt>{t("backtest.account.firstDate")}</dt>
                  <dd>{summary.first_snapshot_date}</dd>
                </div>
                <div>
                  <dt>{t("backtest.account.lastDate")}</dt>
                  <dd>{summary.last_snapshot_date}</dd>
                </div>
                <div>
                  <dt>{t("backtest.account.snapshotCount")}</dt>
                  <dd>{summary.snapshot_count}</dd>
                </div>
              </dl>
            </>
          ) : null}
          <details className="ui-disclosure">
            <summary>{t("backtest.account.provenance")}</summary>
            <dl className="account-report__records">
              <div>
                <dt>run_id</dt>
                <dd>{report.run_id}</dd>
              </div>
              {Object.entries(report.provenance).map(([key, value]) => (
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
          </details>
          <details className="ui-disclosure">
            <summary>
              {t("backtest.account.warnings").replace(
                "{count}",
                String(report.warnings.length),
              )}
            </summary>
            <ol>
              {report.warnings.map((warning, index) => (
                <li key={`${warning.code}-${index}`}>
                  <code>{warning.code}</code> <span>{warning.message}</span>
                </li>
              ))}
            </ol>
          </details>
        </>
      ) : null}
    </div>
  );
};
