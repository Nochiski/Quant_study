import type {
  BacktestRunSummary,
  Experiment,
  StrategySummary,
} from "../../../shared/api";

/** 후보 선택을 기다리는 실험: 끝났는데 고른 후보가 없다(실험 목록의 같은 배지와 같은 조건). */
export const awaitingCandidate = (experiments: readonly Experiment[]): Experiment[] =>
  experiments.filter(
    (experiment) => experiment.status === "completed" && experiment.selections.length === 0,
  );

/** 작업 기록 한 줄. 무엇이 일어났는지와 시각만 담고 문장은 화면이 고른다. */
export type ActivityEntry =
  | { kind: "revision"; at: string; strategyId: string; title: string; revision: number }
  | { kind: "backtest"; at: string; summary: BacktestRunSummary }
  | { kind: "experiment"; at: string; experimentId: string }
  | {
      kind: "selection";
      at: string;
      experimentId: string;
      strategyId: string;
      revision: number;
    };

/**
 * 리비전 저장·백테스트 종료·실험 시작·후보 선택을 시간순(최근부터)으로 합친다(RD-01-D). 리비전은 전략 목록의
 * 최신 저장만 쓴다 — 전략마다 이력을 다시 읽지 않는다. 끝나지 않은 백테스트는 아직 기록이 아니다.
 */
export const activityLog = (
  sources: {
    strategies: readonly StrategySummary[];
    backtests: readonly BacktestRunSummary[];
    experiments: readonly Experiment[];
  },
  limit: number,
): ActivityEntry[] => {
  const entries: ActivityEntry[] = [
    ...sources.strategies.map(
      (strategy): ActivityEntry => ({
        kind: "revision",
        at: strategy.updated_at,
        strategyId: strategy.strategy_id,
        title: strategy.title,
        revision: strategy.latest_revision,
      }),
    ),
    ...sources.backtests
      .filter((summary) => summary.run.status === "completed" || summary.run.status === "failed")
      .map((summary): ActivityEntry => ({ kind: "backtest", at: summary.run.updated_at, summary })),
    ...sources.experiments.flatMap((experiment): ActivityEntry[] => [
      {
        kind: "experiment",
        at: experiment.record.created_at,
        experimentId: experiment.record.experiment_id,
      },
      ...experiment.selections.map(
        (selection): ActivityEntry => ({
          kind: "selection",
          at: selection.selected_at,
          experimentId: selection.experiment_id,
          strategyId: selection.strategy_id,
          revision: selection.revision,
        }),
      ),
    ]),
  ];
  return entries.sort((left, right) => right.at.localeCompare(left.at)).slice(0, limit);
};
