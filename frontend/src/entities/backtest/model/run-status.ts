import type { BacktestRunState } from "../../../shared/api";
import { t } from "../../../shared/config";

/** 생성 계약의 모든 상태를 실행 중 여부와 표시 색에 한 번만 대응한다. */
export const RUN_STATUS = {
  queued: { active: true, tone: "info" },
  running: { active: true, tone: "info" },
  cancel_requested: { active: true, tone: "warn" },
  cancelled: { active: false, tone: "warn" },
  failed: { active: false, tone: "error" },
  completed: { active: false, tone: "ok" },
} as const satisfies Record<BacktestRunState["status"], { active: boolean; tone: string }>;

/** 생성 SDK의 상태·단계 유니온이 번역 누락을 typecheck에서 드러낸다. */
export const runStatusLabel = (status: BacktestRunState["status"]): string => t(`backtest.status.${status}`);
export const runStageLabel = (stage: BacktestRunState["stage"]): string => t(`backtest.stage.${stage}`);
