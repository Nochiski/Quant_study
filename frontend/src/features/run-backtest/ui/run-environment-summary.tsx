import { t } from "../../../shared/config";
import type { BacktestRunSettingsController } from "../model/use-backtest-run-settings";
import {
  runEnvironmentLabel,
  runEnvironmentOptionLabel,
} from "../model/run-environment-labels";
import "./run-environment-summary.css";

type RunEnvironmentSummaryProps = {
  controller: BacktestRunSettingsController;
};

/**
 * IDE 상단의 실행 설정 요약 띠(P3-02, 시안 1). 지금 실행 요청이 어떤 환경으로 나갈지 보이고, 그 값이
 * 전략 문서 밖에 있다는 것을 문장으로 말한다. 필드 목록은 패널과 같이 실행 설정 스키마에서 읽는다.
 */
export const RunEnvironmentSummary = ({
  controller,
}: RunEnvironmentSummaryProps) => {
  const {
    environment,
    environmentFields,
    environmentValues,
    schemaStatus,
    missingLabels,
    openPanelAtFirstProblem,
  } = controller;
  return (
    <section
      className="run-environment-summary"
      aria-label={t("runEnvironment.summary.label")}
    >
      <strong>{t("runEnvironment.summary.title")}</strong>
      {schemaStatus !== "ready" ? (
        <span>
          {schemaStatus === "error"
            ? t("backtest.settings.environment.schemaError")
            : t("backtest.settings.environment.schemaLoading")}
        </span>
      ) : environment === null ? (
        <>
          <span className="run-environment-summary__incomplete" role="status">
            {missingLabels.length > 0
              ? t("runEnvironment.summary.incomplete").replace(
                  "{fields}",
                  missingLabels.join("·"),
                )
              : t("backtest.settings.blocked")}
          </span>
          <button
            type="button"
            className="run-environment-summary__fill"
            onClick={openPanelAtFirstProblem}
          >
            {t("runEnvironment.summary.fill")}
          </button>
        </>
      ) : (
        <dl>
          {environmentFields.map((field) => {
            const value = environmentValues[field.name] ?? "";
            return (
              <div key={field.name}>
                <dt>{runEnvironmentLabel(field)}</dt>
                <dd>
                  {field.control === "select"
                    ? runEnvironmentOptionLabel(field, value)
                    : value}
                </dd>
              </div>
            );
          })}
        </dl>
      )}
      <small>{t("runEnvironment.summary.outside")}</small>
    </section>
  );
};
