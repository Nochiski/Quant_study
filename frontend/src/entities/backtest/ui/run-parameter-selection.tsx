import { ApiRequestError, failureReason } from "../../../shared/api";
import { t } from "../../../shared/config";
import { Button, FailureNotice } from "../../../shared/ui";
import { backtestErrorSentence } from "../model/backtest-error";
import type { useRunParameters } from "../model/use-run-parameters";
import "./run-parameter-selection.css";

/** 선택 출처와 적용 범위를 문서 전체에서 읽는다. 응답 값은 편집·재계산하지 않는다. */
export const RunParameterSelection = ({ selection, clear }: {
  selection: ReturnType<typeof useRunParameters>;
  clear: () => void;
}) => {
  if (selection.state === "default") return null;
  const canRetry = selection.error !== null && (!(selection.error instanceof ApiRequestError) || selection.error.status === 0 || selection.error.status >= 500);
  return <section className="run-parameter-selection" aria-label={t("backtest.parameters.title")}>
    <details className="ui-disclosure">
      <summary aria-label={`${t("backtest.parameters.summary")}: ${t("backtest.parameters.basis")}`}>{t("backtest.parameters.summary")}</summary>
      <div tabIndex={0} role="region" aria-label={t("backtest.parameters.basis")}>
        <p>{t("backtest.parameters.source")}: <code>{selection.runId}</code></p>
        <p>{t("backtest.parameters.scope")}</p>
        {selection.state === "selected" ? Object.keys(selection.values ?? {}).length === 0
          ? <p>{t("backtest.parameters.empty")}</p>
          : <dl>{Object.entries(selection.values ?? {}).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{String(value)}</dd></div>)}</dl>
          : null}
      </div>
    </details>
    <Button className="run-parameter-selection__clear" aria-label={t("backtest.parameters.clear")} onClick={clear}>{t("backtest.parameters.clearShort")}</Button>
    {selection.state === "loading" ? <p role="status">{t("backtest.parameters.loading")}</p> : null}
    {selection.state === "mismatch" ? <p role="alert">{t("backtest.parameters.mismatch")}</p> : null}
    {selection.state === "error" ? <FailureNotice
      message={(selection.error instanceof ApiRequestError ? backtestErrorSentence(selection.error.code ?? null) : null) ?? t("backtest.parameters.failed")}
      reason={failureReason(selection.error)}
    >{canRetry ? <Button onClick={selection.retry}>{t("backtest.parameters.retry")}</Button> : null}</FailureNotice> : null}
  </section>;
};
