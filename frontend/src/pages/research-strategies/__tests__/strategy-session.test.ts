import { describe, expect, it } from "vitest";

import type {
  BacktestRunSummary,
  Experiment,
  RevisionSummary,
  TrialLedger,
} from "../../../shared/api";
import { ledgerRoleCounts, sessionVersions } from "../model/strategy-session";

const revision = (number: number): RevisionSummary =>
  ({ revision: number, change_note: `v${number}`, created_at: `2026-10-0${number}T00:00:00Z` }) as RevisionSummary;

const backtest = (
  runId: string,
  createdAt: string,
  provenance: Partial<BacktestRunSummary["strategy_provenance"]>,
): BacktestRunSummary =>
  ({
    run: { run_id: runId, created_at: createdAt },
    strategy_provenance: { kind: "saved_revision", strategy_id: "alpha", ...provenance },
  }) as BacktestRunSummary;

const experiment = (id: string, createdAt: string, strategyId: string, number: number): Experiment =>
  ({
    record: {
      experiment_id: id,
      created_at: createdAt,
      run: { strategy_source: { kind: "saved_revision", strategy_id: strategyId, revision: number } },
    },
  }) as unknown as Experiment;

describe("sessionVersions", () => {
  it("버전을 최신부터 두고, 그 버전으로 돌린 백테스트와 실험을 최근 것부터 묶는다", () => {
    const versions = sessionVersions(
      "alpha",
      [revision(1), revision(3), revision(2)],
      [
        backtest("run-a", "2026-10-05T00:00:00Z", { revision: 2 }),
        backtest("run-b", "2026-10-07T00:00:00Z", { revision: 3 }),
      ],
      [experiment("exp-a", "2026-10-06T00:00:00Z", "alpha", 2)],
    );
    expect(versions.map((version) => version.revision.revision)).toEqual([3, 2, 1]);
    expect(versions[1]!.entries.map((entry) => entry.kind)).toEqual(["experiment", "backtest"]);
    expect(versions[0]!.entries).toHaveLength(1);
    expect(versions[2]!.entries).toEqual([]);
  });

  it("다른 전략·저장하지 않은 초안으로 돈 일은 넣지 않는다", () => {
    const versions = sessionVersions(
      "alpha",
      [revision(1)],
      [
        backtest("other", "2026-10-05T00:00:00Z", { strategy_id: "beta", revision: 1 }),
        backtest("draft", "2026-10-05T00:00:00Z", { kind: "inline_draft", revision: null }),
      ],
      [experiment("exp-b", "2026-10-06T00:00:00Z", "beta", 1)],
    );
    expect(versions[0]!.entries).toEqual([]);
  });
});

describe("ledgerRoleCounts", () => {
  it("원장 실행을 역할마다 센다", () => {
    const ledger = {
      trials: [
        { runs: [{ role: "counted" }, { role: "recheck" }] },
        { runs: [{ role: "counted" }, { role: "no_result" }] },
      ],
    } as unknown as TrialLedger;
    expect(ledgerRoleCounts(ledger)).toEqual({ counted: 2, recheck: 1, pending: 0, no_result: 1 });
  });
});
