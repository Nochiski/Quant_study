import type { RunKind } from "../../../shared/api";
import { t, type MessageKey } from "../../../shared/config";

/**
 * 실행 종류의 쉬운 이름(검증 랩 V5-03). 종류는 backend 가 정해 `BacktestRunSummary.kind` 로 싣고, 화면은
 * 이름만 붙인다. 생성 SDK 의 `RunKind` 가 늘었는데 이름이 없으면 이 표가 typecheck 에서 깨진다.
 */
const RUN_KIND_LABELS: Record<RunKind, MessageKey> = {
  single: "backtest.runKind.single",
  experiment_trial: "backtest.runKind.experiment_trial",
  walk_forward_validation: "backtest.runKind.walk_forward_validation",
};

/** 백테스트 이력 종류 필터의 순서. */
export const RUN_KINDS = Object.keys(RUN_KIND_LABELS) as RunKind[];

export const runKindLabel = (kind: RunKind): string => t(RUN_KIND_LABELS[kind]);

export const isRunKind = (value: unknown): value is RunKind =>
  typeof value === "string" && Object.hasOwn(RUN_KIND_LABELS, value);
