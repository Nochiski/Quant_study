import { t } from "../../../shared/config";
import type { BacktestRunSettingsController } from "../model/use-backtest-run-settings";
import {
  runEnvironmentLabel,
  runEnvironmentValueLabel,
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
    schemaStatus,
    blockedReason,
    problemKind,
    openPanelAtFirstProblem,
  } = controller;
  const briefFields = new Set(["start", "end", "universe_id", "fee_bps", "slippage_bps"]);
  const values = (brief: boolean) => (
    <dl>
      {environmentFields.map((field) => {
        if (environment === null || !(field.name in environment) || (brief && !briefFields.has(field.name))) return null;
        const value = (environment as unknown as Record<string, unknown>)[field.name];
        return <div key={field.name}><dt>{runEnvironmentLabel(field)}</dt><dd>{runEnvironmentValueLabel(field, value)}</dd></div>;
      })}
    </dl>
  );
  return (
    <section
      className="run-environment-summary"
      aria-label={t("runEnvironment.summary.label")}
    >
      <strong>{t("runEnvironment.summary.title")}</strong>
      {schemaStatus !== "ready" ? (
        <span className="run-environment-summary__schema">
          {schemaStatus === "error"
            ? t("backtest.settings.environment.schemaError")
            : t("backtest.settings.environment.schemaLoading")}
        </span>
      ) : environment === null ? null : (
        values(true)
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
      {schemaStatus === "ready" && environment !== null && blockedReason === null ? <span className="run-environment-summary__mobile-state">{t("toolbar.environmentReady")}</span> : null}
      <details
        className="run-environment-summary__details ui-disclosure"
        onBlurCapture={(event) => {
          if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false;
        }}
      >
        <summary>{t("toolbar.environmentDetails")}</summary>
        <div className="run-environment-summary__all">
          <p>{t("runEnvironment.summary.outside")}</p>
          {environment === null ? null : values(false)}
        </div>
      </details>
    </section>
  );
};
