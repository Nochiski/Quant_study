import type { Experiment } from "../../../shared/api";
import "./trial-progress.css";

type TrialCounts = Experiment["trial_counts"];

/** 끝난 몫(완료·실패·취소)과 남은 몫을 한 막대로 보인다. 수는 서버 `trial_counts` 그대로다. */
export const TrialProgress = ({ counts }: { counts: TrialCounts }) => {
  const total = Object.values(counts).reduce((sum, count) => sum + (count ?? 0), 0);
  const part = (key: keyof TrialCounts) =>
    total === 0 ? 0 : ((counts[key] ?? 0) / total) * 100;
  return (
    <span className="experiment-progress" aria-hidden="true">
      <span className="experiment-progress__completed" style={{ width: `${part("completed")}%` }} />
      <span className="experiment-progress__failed" style={{ width: `${part("failed")}%` }} />
      <span className="experiment-progress__running" style={{ width: `${part("running")}%` }} />
    </span>
  );
};
