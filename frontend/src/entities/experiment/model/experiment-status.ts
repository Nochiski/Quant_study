import type {
  Experiment,
  ExperimentTrialState,
  ParameterMap,
  WalkForwardReport,
} from "../../../shared/api";
import { t, tFill, type MessageKey } from "../../../shared/config";
import type { BadgeTone } from "../../../shared/ui";

type ExperimentStatus = Experiment["status"];

// 실험 상태는 backend 가 trial 상태·취소·일시정지에서 파생한다(spec D6). 화면은 이름과 색만 붙인다.
// 생성 SDK 의 상태가 늘면 이 표가 typecheck 에서 깨진다.
const STATUS: Record<ExperimentStatus, { label: MessageKey; tone: BadgeTone }> =
  {
    queued: { label: "experiments.status.queued", tone: "info" },
    running: { label: "experiments.status.running", tone: "info" },
    paused: { label: "experiments.status.paused", tone: "warn" },
    completed: { label: "experiments.status.completed", tone: "ok" },
    cancelled: { label: "experiments.status.cancelled", tone: "error" },
  };

export const experimentStatusLabel = (status: ExperimentStatus): string =>
  t(STATUS[status].label);

export const experimentStatusTone = (status: ExperimentStatus): BadgeTone =>
  STATUS[status].tone;

type TrialStatus = ExperimentTrialState["status"];
type Gap = NonNullable<WalkForwardReport["gap"]>;
type RetentionGap = NonNullable<WalkForwardReport["retention_gap"]>;

// trial 상태도 backend 파생이다(최신 attempt 의 실행 상태).
const TRIAL: Record<TrialStatus, { label: MessageKey; tone: BadgeTone }> = {
  queued: { label: "experiments.trial.queued", tone: "neutral" },
  running: { label: "experiments.trial.running", tone: "info" },
  completed: { label: "experiments.trial.completed", tone: "ok" },
  failed: { label: "experiments.trial.failed", tone: "error" },
  cancelled: { label: "experiments.trial.cancelled", tone: "warn" },
};

export const trialStatusLabel = (status: TrialStatus): string =>
  t(TRIAL[status].label);

export const trialStatusTone = (status: TrialStatus): BadgeTone =>
  TRIAL[status].tone;

// 워크포워드 요약이 비는 이유(backend `WalkForwardGap`). 화면은 번역만 한다.
const GAP: Record<Gap, MessageKey> = {
  legacy_design: "experiments.gap.legacy_design",
  cancelled: "experiments.gap.cancelled",
  result_unreadable: "experiments.gap.result_unreadable",
  test_failed: "experiments.gap.test_failed",
  no_cell: "experiments.gap.no_cell",
  pending: "experiments.gap.pending",
};

export const walkForwardGapLabel = (gap: Gap): string => t(GAP[gap]);

// 곡선은 났는데 유지율만 비는 이유(backend `RetentionGap`). "—"만 보이면 계산 실패로 읽힌다.
const RETENTION_GAP: Record<RetentionGap, MessageKey> = {
  out_of_sample_unavailable:
    "experiments.retentionGap.out_of_sample_unavailable",
  no_train_sharpe: "experiments.retentionGap.no_train_sharpe",
  non_positive_train_sharpe:
    "experiments.retentionGap.non_positive_train_sharpe",
};

/** 유지율이 비었으면 그 이유 문장, 아니면 null. 옛 서버(이유 칸 없음)도 null 이다. */
export const retentionGapLabel = (
  report: Pick<WalkForwardReport, "retention_gap" | "train_sharpe_mean">,
): string | null =>
  report.retention_gap == null
    ? null
    : tFill(RETENTION_GAP[report.retention_gap], {
        mean: report.train_sharpe_mean?.toFixed(4) ?? "—",
      });

type NoRecommendation = NonNullable<ParameterMap["no_recommendation"]>;

// 파라미터 지도에 추천 칸이 없는 이유(backend `NoRecommendation`). 화면은 번역만 한다.
const NO_RECOMMENDATION: Record<NoRecommendation, MessageKey> = {
  no_scored_cell: "experiments.noRecommendation.no_scored_cell",
  non_positive_score: "experiments.noRecommendation.non_positive_score",
};

/** 추천 칸이 없는 이유 문장. 이유 칸이 없는 옛 서버면 일반 문장이다. */
export const noRecommendationLabel = (
  reason: ParameterMap["no_recommendation"],
): string =>
  t(
    reason == null
      ? "experiments.noRecommendation.unknown"
      : NO_RECOMMENDATION[reason],
  );
