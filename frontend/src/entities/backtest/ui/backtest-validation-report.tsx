import { ApiRequestError, type RunValidationReport } from "../../../shared/api";
import { t, tCode } from "../../../shared/config";
import { displayTime } from "../../../shared/lib/display-time";
import { Button } from "../../../shared/ui";
import { metricUnavailableCopy } from "../model/metric-copy";
import { BacktestResultFailure } from "./backtest-run-failure";
import "./backtest-validation-report.css";

/** 카드에만 상한을 표시한다. 원시 지표 표와 계산 근거의 값은 다시 반올림하지 않는다. */
const statistic = (value: number | null) =>
  value === null ? t("backtest.validation.noValue")
    : value > 0.9999 ? ">99.99%" : `${(value * 100).toFixed(2)}%`;

/** 수치·사유·원장 근거는 생성 SDK 응답만 읽고 화면에서 통계를 다시 계산하지 않는다. */
export const BacktestValidationReport = ({
  report,
  loading,
  fetching,
  error,
  refresh,
}: {
  report: RunValidationReport | undefined;
  loading: boolean;
  fetching: boolean;
  error: unknown;
  refresh: () => void;
}) => {
  const canRefresh = !(error instanceof ApiRequestError) || error.status === 0 || error.status >= 500;
  // 파일 손실 뒤에는 앞서 조회한 통계도 현재 조회 성공인 것처럼 보이지 않는다.
  const evidence = error instanceof ApiRequestError && error.status === 410 ? undefined : report;
  const dsr = evidence?.dsr;
  return (
    <div className="validation-report" aria-busy={fetching}>
      <header className="validation-report__header">
        <h4>{t("backtest.validation.title")}</h4>
        {canRefresh && !loading ? (
          <Button disabled={fetching} onClick={refresh}>
            {t(fetching ? "backtest.validation.refreshing" : "backtest.validation.refresh")}
          </Button>
        ) : null}
      </header>
      <p>{t("backtest.validation.scope")}</p>
      {loading ? <p role="status">{t("backtest.validation.loading")}</p> : null}
      {error !== null ? (
        <div>
          <p>{t("backtest.validation.failed")}</p>
          <BacktestResultFailure error={error} />
        </div>
      ) : null}
      {evidence && dsr ? (
        <>
          <p>{t("backtest.validation.currentLedger")}</p>
          {error !== null ? <p>{t("backtest.validation.lastConfirmed")}</p> : null}
          <p className="validation-report__observed">
            {t("backtest.validation.observedAt")}: <time dateTime={evidence.observed_at}>{displayTime(evidence.observed_at, { seconds: true })}</time>
          </p>
          {dsr.registry_mixed ? <p className="validation-report__warning">{t("backtest.validation.registryMixed")}</p> : null}
          {dsr.unknown_registry_count > 0 ? <p className="validation-report__warning">{t("backtest.validation.registryUnknown")}</p> : null}
          <div className="validation-report__statistics">
            <section aria-label={t("backtest.validation.psr")}>
              <h5>{t("backtest.validation.psr")}</h5>
              <div className="validation-report__value">{statistic(evidence.psr?.value ?? null)}</div>
              <p>{t("backtest.validation.psrMeaning")}</p>
              {evidence.psr === null ? <p>{t("backtest.validation.notRecorded")}</p> : (
                <>
                  {evidence.psr.value === null && evidence.psr.unavailable_reason ? <p>{metricUnavailableCopy(evidence.psr.unavailable_reason)}</p> : null}
                  <p>{t("backtest.validation.samples")}: {evidence.psr.sample_count}</p>
                </>
              )}
            </section>
            <section aria-label={t("backtest.validation.dsr")}>
              <h5>{t("backtest.validation.dsr")}</h5>
              <div className="validation-report__value">{statistic(dsr.value)}</div>
              <p>{t("backtest.validation.dsrMeaning")}</p>
              {dsr.unavailable_reason ? <p>{tCode(`backtest.validation.reason.${dsr.unavailable_reason}`, dsr.unavailable_reason)}</p> : null}
              <p>{t("backtest.validation.trialCount")}: {dsr.trial_count ?? t("backtest.validation.noValue")}</p>
              <p>{t("backtest.validation.representativeCount")}: {dsr.representative_count}</p>
            </section>
          </div>
          <details className="ui-disclosure">
            <summary>{t("backtest.validation.basis")}</summary>
            <p>{t("backtest.validation.assumptions")}</p>
            <p>{t("backtest.validation.representativePolicy")}</p>
            <dl className="validation-report__evidence">
              <div><dt>{t("backtest.validation.observedAt")}</dt><dd>{evidence.observed_at}</dd></div>
              <div><dt>{t("backtest.validation.rawPsr")}</dt><dd>{evidence.psr?.value ?? t("backtest.validation.noValue")}</dd></div>
              <div><dt>{t("backtest.validation.rawDsr")}</dt><dd>{dsr.value ?? t("backtest.validation.noValue")}</dd></div>
              <div><dt>{t("backtest.validation.runId")}</dt><dd>{evidence.run_id}</dd></div>
              <div><dt>{t("backtest.validation.fingerprint")}</dt><dd>{evidence.run_fingerprint}</dd></div>
              <div><dt>{t("backtest.validation.runRegistry")}</dt><dd>{evidence.metric_registry_version}</dd></div>
              <div><dt>{t("backtest.validation.lineage")}</dt><dd>{dsr.lineage_id ?? t("backtest.validation.noValue")}</dd></div>
              <div><dt>{t("backtest.validation.mergedLineages")}</dt><dd>{dsr.merged_lineage_ids.join("\n") || "—"}</dd></div>
              <div><dt>{t("backtest.validation.registryVersions")}</dt><dd>{dsr.representative_registry_versions.join("\n") || "—"}</dd></div>
              <div><dt>{t("backtest.validation.unknownRegistryCount")}</dt><dd>{dsr.unknown_registry_count}</dd></div>
              <div><dt>{t("backtest.validation.annualization")}</dt><dd>{dsr.annualization_days}</dd></div>
              <div><dt>{t("backtest.validation.variance")}</dt><dd>{dsr.trial_sharpe_variance ?? t("backtest.validation.noValue")}</dd></div>
              <div><dt>{t("backtest.validation.referenceSharpe")}</dt><dd>{dsr.expected_maximum_sharpe ?? t("backtest.validation.noValue")}</dd></div>
              <div><dt>{t("backtest.validation.sessionSharpe")}</dt><dd>{dsr.session_sharpe ?? t("backtest.validation.noValue")}</dd></div>
              <div><dt>{t("backtest.validation.sessionError")}</dt><dd>{dsr.session_standard_error ?? t("backtest.validation.noValue")}</dd></div>
            </dl>
          </details>
        </>
      ) : null}
    </div>
  );
};
