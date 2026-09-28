import { t } from "../../../shared/config";
import type { BacktestRunSettingsController } from "../model/use-backtest-run-settings";
import {
  runEnvironmentLabel,
  runEnvironmentOptionLabel,
} from "../../../entities/backtest";
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
    blockedReason,
    problemKind,
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
      ) : environment === null ? null : (
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
      {/* 막힌 이유는 툴바 tooltip 이 아니라 여기서 보이는 문장이다. 버튼이 그 칸으로 초점을 옮긴다
          (DEFECT-242-01). 문장은 툴바·"적용 후 백테스트" 알림과 같은 `blockedReason` 이다. */}
      {schemaStatus === "ready" &&
      blockedReason !== null &&
      problemKind !== null ? (
        <>
          <span className="run-environment-summary__incomplete" role="status">
            {blockedReason}
          </span>
          <button
            type="button"
            className="run-environment-summary__fill"
            onClick={openPanelAtFirstProblem}
          >
            {t(
              problemKind === "missing"
                ? "runEnvironment.summary.fill"
                : "runEnvironment.summary.fix",
            )}
          </button>
        </>
      ) : null}
      <small>{t("runEnvironment.summary.outside")}</small>
    </section>
  );
};
