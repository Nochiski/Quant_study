import { useState } from "react";

import {
  BacktestRunDetail,
  useBacktestResult,
  useBacktestStatus,
  useCancelBacktest,
  useStartBacktest,
} from "../../../entities/backtest";
import type { StrategySpec } from "../../../entities/strategy";
import { Button } from "../../../shared/ui";

export const BacktestRunner = ({ strategy }: { strategy: StrategySpec }) => {
  const [runId, setRunId] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get("run"),
  );
  const [core, setCore] = useState<"rust" | "python">("rust");
  const [initialCash, setInitialCash] = useState(100_000_000);
  const [benchmark, setBenchmark] = useState("005930");
  const [oosStart, setOosStart] = useState("");
  const start = useStartBacktest();
  const status = useBacktestStatus(runId);
  const cancel = useCancelBacktest();
  const state = status.data ?? start.data?.run;
  const completed = state?.status === "completed";
  const result = useBacktestResult(runId, completed);
  const active =
    state?.status === "queued" ||
    state?.status === "running" ||
    state?.status === "cancel_requested";

  const run = async () => {
    const metricWindows =
      oosStart === ""
        ? []
        : [
            {
              scope: "out_of_sample" as const,
              start: oosStart,
              end: strategy.data.end,
              label: `OOS ${oosStart}`,
            },
          ];
    const accepted = await start.mutateAsync({
      strategy,
      core,
      initial_cash: initialCash,
      benchmark_security_id: benchmark === "" ? null : benchmark,
      metric_windows: metricWindows,
    });
    setRunId(accepted.run.run_id);
  };

  return (
    <section className="backtest-workspace">
      <header className="backtest-workspace__header">
        <div>
          <span className="section-kicker">PERSISTENT RUST ENGINE</span>
          <h2>전략 실행과 전문 성과 분석</h2>
          <p>
            현재 StrategySpec을 TargetTape로 컴파일한 뒤 엔진에서 실행합니다.
            Python core는 패리티 디버깅용입니다.
          </p>
        </div>
        <span className="engine-badge engine-badge--ready">
          MetricRegistry v1 · 21 metrics
        </span>
      </header>

      <section className="run-console">
        <div className="run-controls">
          <label>
            실행 core
            <select
              aria-label="실행 core"
              disabled={active}
              onChange={(event) =>
                setCore(event.target.value as "rust" | "python")
              }
              value={core}
            >
              <option value="rust">Persistent Rust</option>
              <option value="python">Python reference</option>
            </select>
          </label>
          <label>
            초기 자본 (KRW)
            <input
              aria-label="초기 자본 (KRW)"
              disabled={active}
              min={1}
              onChange={(event) => setInitialCash(Number(event.target.value))}
              type="number"
              value={initialCash}
            />
          </label>
          <label>
            벤치마크 종목 ID
            <input
              aria-label="벤치마크 종목 ID"
              disabled={active}
              onChange={(event) => setBenchmark(event.target.value)}
              value={benchmark}
            />
          </label>
          <label>
            OOS 시작일 (선택)
            <input
              aria-label="OOS 시작일 (선택)"
              disabled={active}
              max={strategy.data.end}
              min={strategy.data.start}
              onChange={(event) => setOosStart(event.target.value)}
              type="date"
              value={oosStart}
            />
          </label>
        </div>
        <div className="run-actions">
          <Button
            disabled={active || start.isPending}
            onClick={() => void run()}
            tone="primary"
          >
            {completed ? "새 백테스트 실행" : "백테스트 실행"}
          </Button>
          {active && runId !== null && (
            <Button
              disabled={
                cancel.isPending || state?.status === "cancel_requested"
              }
              onClick={() => void cancel.mutateAsync(runId)}
            >
              실행 취소
            </Button>
          )}
          <span>
            {strategy.data.start} → {strategy.data.end}
          </span>
        </div>

        {state !== undefined && (
          <div className="run-progress" aria-live="polite">
            <div>
              <strong>{state.status.replaceAll("_", " ")}</strong>
              <span>{state.message}</span>
              <code>{state.stage}</code>
            </div>
            <progress max={1} value={state.progress} />
            <small>{Math.round(state.progress * 100)}%</small>
          </div>
        )}
        {(start.isError || status.isError || result.isError) && (
          <p className="inline-state inline-state--error">
            실행 요청 또는 결과 조회에 실패했습니다. 입력 조건과 API 상태를
            확인하세요.
          </p>
        )}
        {state?.status === "failed" && (
          <p className="inline-state inline-state--error">{state.error}</p>
        )}
      </section>

      {result.isPending && completed && (
        <p className="state-message">결과 artifact를 불러오는 중입니다.</p>
      )}
      {result.data !== undefined && <BacktestRunDetail result={result.data} />}
    </section>
  );
};
