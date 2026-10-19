import { describe, expect, it } from "vitest";

import type { BacktestRunSummary, Experiment, StrategySummary } from "../../../shared/api";
import { activityLog, awaitingCandidate } from "../model/home-model";

const run = (runId: string, status: string, updatedAt: string) =>
  ({ run: { run_id: runId, status, updated_at: updatedAt } }) as unknown as BacktestRunSummary;

const experiment = (id: string, createdAt: string, selectedAt: string | null, status = "completed") =>
  ({
    status,
    record: { experiment_id: id, created_at: createdAt },
    selections:
      selectedAt === null
        ? []
        : [{ experiment_id: id, strategy_id: "s1", revision: 2, selected_at: selectedAt }],
  }) as unknown as Experiment;

describe("awaitingCandidate", () => {
  it("끝났는데 고른 후보가 없는 실험만 고른다", () => {
    const items = [
      experiment("done", "2026-09-01T00:00:00Z", null),
      experiment("picked", "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"),
      experiment("running", "2026-09-01T00:00:00Z", null, "running"),
    ];
    expect(awaitingCandidate(items).map((item) => item.record.experiment_id)).toEqual(["done"]);
  });
});

describe("activityLog", () => {
  it("저장·끝난 백테스트·실험 시작·후보 선택을 최근부터 합치고 도는 실행은 뺀다", () => {
    const log = activityLog(
      {
        strategies: [
          {
            strategy_id: "s1",
            title: "알파",
            latest_revision: 3,
            updated_at: "2026-09-04T00:00:00Z",
          } as StrategySummary,
        ],
        backtests: [
          run("done", "completed", "2026-09-05T00:00:00Z"),
          run("live", "running", "2026-09-07T00:00:00Z"),
        ],
        experiments: [experiment("exp", "2026-09-03T00:00:00Z", "2026-09-06T00:00:00Z")],
      },
      10,
    );
    expect(log.map((entry) => entry.kind)).toEqual([
      "selection",
      "backtest",
      "revision",
      "experiment",
    ]);
    expect(activityLog({ strategies: [], backtests: [], experiments: [] }, 10)).toEqual([]);
  });
});
