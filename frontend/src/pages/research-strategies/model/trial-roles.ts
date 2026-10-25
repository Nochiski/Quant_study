import type { TrialRunRole } from "../../../shared/api";
import { t, type MessageKey } from "../../../shared/config";
import type { BadgeTone } from "../../../shared/ui";

// 실행 역할은 backend 원장 집계(`summarize_trial_ledger`)가 정한다. 화면은 이름과 색만 붙인다.
export const ROLE_TONE: Record<TrialRunRole, BadgeTone> = {
  counted: "ok",
  recheck: "info",
  pending: "neutral",
  no_result: "warn",
};

export const roleLabel = (role: TrialRunRole): string => {
  const key: MessageKey = `history.trials.role.${role}`;
  return t(key);
};

