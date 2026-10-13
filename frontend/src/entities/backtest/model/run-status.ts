import type { BacktestRunState } from "../../../shared/api";

/** 생성 계약의 모든 상태를 실행 중 여부와 표시 색에 한 번만 대응한다. */
export const RUN_STATUS = {
  queued: { active: true, tone: "info" },
  running: { active: true, tone: "info" },
  cancel_requested: { active: true, tone: "warn" },
  cancelled: { active: false, tone: "warn" },
  failed: { active: false, tone: "error" },
  completed: { active: false, tone: "ok" },
} as const satisfies Record<BacktestRunState["status"], { active: boolean; tone: string }>;
