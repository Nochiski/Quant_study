import type {
  BacktestRunSummary,
  Experiment,
  RevisionSummary,
  TrialLedger,
  TrialRunRole,
} from "../../../shared/api";

/** 버전 아래에 묶이는 일: 그 버전으로 돌린 단일 백테스트와, 그 버전을 기반으로 만든 실험. */
export type SessionEntry =
  | { kind: "backtest"; at: string; summary: BacktestRunSummary }
  | { kind: "experiment"; at: string; experiment: Experiment };

export type SessionVersion = {
  revision: RevisionSummary;
  entries: SessionEntry[];
};

/** 처음부터 펼쳐 둘 최신 버전 수. 나머지는 "이전 버전 펼치기" 뒤에 둔다(RD-03-A). */
export const RECENT_VERSIONS = 3;

const revisionOfBacktest = (
  strategyId: string,
  summary: BacktestRunSummary,
): number | null => {
  const provenance = summary.strategy_provenance;
  return provenance.kind === "saved_revision" &&
    provenance.strategy_id === strategyId &&
    provenance.revision != null
    ? provenance.revision
    : null;
};

const revisionOfExperiment = (
  strategyId: string,
  experiment: Experiment,
): number | null => {
  const source = experiment.record.run.strategy_source;
  return source?.kind === "saved_revision" && source.strategy_id === strategyId
    ? source.revision
    : null;
};

/**
 * 한 전략의 버전마다 그 버전으로 돌린 일을 묶는다. 버전은 최신부터, 버전 안의 일은 최근에 만든 것부터다.
 * 어느 버전에 속하는지는 실행의 출처(`strategy_provenance`)와 실험 기반 요청의 저장 리비전만 읽는다 — 다른
 * 전략이나 저장하지 않은 초안으로 돈 일은 넣지 않는다.
 */
export const sessionVersions = (
  strategyId: string,
  revisions: readonly RevisionSummary[],
  backtests: readonly BacktestRunSummary[],
  experiments: readonly Experiment[],
): SessionVersion[] => {
  const entries = new Map<number, SessionEntry[]>();
  const add = (revision: number | null, entry: SessionEntry) => {
    if (revision === null) return;
    entries.set(revision, [...(entries.get(revision) ?? []), entry]);
  };
  for (const summary of backtests)
    add(revisionOfBacktest(strategyId, summary), {
      kind: "backtest",
      at: summary.run.created_at,
      summary,
    });
  for (const experiment of experiments)
    add(revisionOfExperiment(strategyId, experiment), {
      kind: "experiment",
      at: experiment.record.created_at,
      experiment,
    });
  return [...revisions]
    .sort((left, right) => right.revision - left.revision)
    .map((revision) => ({
      revision,
      entries: [...(entries.get(revision.revision) ?? [])].sort((left, right) =>
        right.at.localeCompare(left.at),
      ),
    }));
};

/** 원장 실행을 역할마다 센다. 역할은 backend 원장 집계가 정했고 여기서는 수만 더한다. */
export const ledgerRoleCounts = (
  ledger: TrialLedger,
): Record<TrialRunRole, number> => {
  const counts: Record<TrialRunRole, number> = {
    counted: 0,
    recheck: 0,
    pending: 0,
    no_result: 0,
  };
  for (const trial of ledger.trials)
    for (const run of trial.runs) counts[run.role] += 1;
  return counts;
};
