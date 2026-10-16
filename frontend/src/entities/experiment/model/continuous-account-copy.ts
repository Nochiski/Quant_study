import type { ContinuousAccountLatest } from "../../../shared/api";
import type { MessageKey } from "../../../shared/config";

type ReadinessCode = NonNullable<ContinuousAccountLatest["readiness"]["code"]>;
export const CONTINUOUS_ACCOUNT_ERROR_KEYS = {
  "continuous.mock_only": "experiments.continuous.mock_only",
  "continuous.unfinished": "experiments.continuous.unfinished",
  "continuous.unsupported": "experiments.continuous.unsupported",
  "continuous.no_cell": "experiments.continuous.no_cell",
  "continuous.source_invalid": "experiments.continuous.source_invalid",
  "continuous.busy": "experiments.continuous.busy",
  "continuous.request_conflict": "experiments.continuous.conflict",
  "continuous.prepare_failed": "experiments.continuous.prepareFailed",
  "continuous.request_invalid": "experiments.continuous.requestInvalid",
  "continuous.execution_failed": "experiments.continuous.failed",
  "continuous.interrupted": "experiments.continuous.interrupted",
  "continuous.not_found": "experiments.continuous.notFound",
  "continuous.result_unreadable": "experiments.continuous.permanent",
} satisfies Record<ReadinessCode, MessageKey>;
/** 서버 오류 어휘와 화면 문구의 연결은 이 owner가 한 번만 소유한다. */
export const continuousAccountErrorKey = (
  code: string | null | undefined,
): MessageKey | undefined =>
  code == null
    ? undefined
    : (CONTINUOUS_ACCOUNT_ERROR_KEYS as Partial<Record<string, MessageKey>>)[
        code
      ];
