import type { Experiment } from "../../../shared/api";
import { t, type MessageKey } from "../../../shared/config";
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

/** 끝난 실험(완료·취소). 목록 다시 묻기와 조작 노출이 같은 판정을 쓴다. */
export const isExperimentSettled = (status: ExperimentStatus): boolean =>
  status === "completed" || status === "cancelled";

export const experimentStatusTone = (status: ExperimentStatus): BadgeTone =>
  STATUS[status].tone;
