import type { StrategyTraceResponse } from "../../../shared/api";
import { t } from "../../../shared/config";
import "./trace-parameters.css";

/** 완전히 해소된 평가 값은 backend 응답에서 읽는다. 문서 기본값을 화면에서 합치지 않는다. */
export const TraceParameters = ({ values }: { values: StrategyTraceResponse["parameter_values"] }) => {
  if (values === undefined || Object.keys(values).length === 0) return null;
  return <details className="ui-disclosure trace-parameters">
    <summary>{t("debugger.parameters.resolved")}</summary>
    <dl>{Object.entries(values).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{String(value)}</dd></div>)}</dl>
  </details>;
};
