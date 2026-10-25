import { useQuery } from "@tanstack/react-query";

import { runStatusLabel } from "../../../entities/backtest";
import {
  experimentAuditQuery,
  type ExperimentAuditEvent,
} from "../../../entities/experiment";
import { failureReason } from "../../../shared/api";
import { Button, FailureNotice } from "../../../shared/ui";
import { t, tFill } from "../../../shared/config";
import { displayTime } from "../../../shared/lib/display-time";

const target = (event: ExperimentAuditEvent): string =>
  [
    event.trial_index == null
      ? null
      : tFill("experiments.audit.trial", { index: event.trial_index + 1 }),
    event.window_index == null
      ? null
      : tFill("experiments.audit.window", { index: event.window_index + 1 }),
    event.attempt == null
      ? null
      : tFill("experiments.audit.attempt", { attempt: event.attempt }),
  ]
    .filter((part) => part !== null)
    .join(" · ") || "—";

/** 사건마다 backend 가 실은 칸만 보인다 — 화면은 판정하지 않는다. */
const detail = (event: ExperimentAuditEvent): string =>
  [
    event.paused == null || event.priority == null
      ? null
      : tFill("experiments.audit.controls", {
          state: t(
            event.paused
              ? "experiments.audit.paused"
              : "experiments.audit.resumed",
          ),
          priority: event.priority,
        }),
    event.run_status == null ? null : runStatusLabel(event.run_status),
    event.run_id ?? null,
    event.code ?? null,
    event.reason ?? null,
  ]
    .filter((part) => part !== null)
    .join(" · ") || "—";

/**
 * 실험 감사 기록(로드맵 M6). 생성·조작·trial attempt·실행 시작과 끝·창 선택·후보 선택·취소를 backend 가
 * 시각 순으로 편 그대로 보인다. 진행 스트림이 실험 조회를 다시 읽을 때 같이 다시 읽는다.
 */
export const AuditTrail = ({
  experimentId,
  refetchInterval,
}: {
  experimentId: string;
  refetchInterval: number | false;
}) => {
  const audit = useQuery({
    ...experimentAuditQuery(experimentId),
    refetchInterval,
  });
  if (audit.isPending)
    return (
      <p className="experiment-section__feedback" role="status">
        {t("page.loading")}
      </p>
    );
  if (audit.isError)
    return (
      <div className="experiment-section__feedback">
        <FailureNotice
          title={t("experiments.audit.title")}
          message={t("experiments.audit.loadFailed")}
          reason={failureReason(audit.error)}
        >
          <Button
            size="small"
            tone="secondary"
            onClick={() => void audit.refetch()}
          >
            {t("experiments.audit.reload")}
          </Button>
        </FailureNotice>
      </div>
    );
  return (
    <div
      className="data-list-page__scroll experiment-table-scroll"
      role="region"
      tabIndex={0}
      aria-label={t("experiments.audit.title")}
    >
      <table className="data-list-page__table">
        <caption>{t("experiments.audit.title")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("experiments.audit.time")}</th>
            <th scope="col">{t("experiments.audit.event")}</th>
            <th scope="col">{t("experiments.audit.target")}</th>
            <th scope="col">{t("experiments.audit.detail")}</th>
          </tr>
        </thead>
        <tbody>
          {audit.data.map((event, index) => (
            <tr key={index}>
              <td>{displayTime(event.occurred_at, { seconds: true })}</td>
              <td>{t(`experiments.audit.kind.${event.kind}`)}</td>
              <td>{target(event)}</td>
              <td>{detail(event)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
