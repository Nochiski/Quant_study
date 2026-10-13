import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";

import { experimentsQuery } from "../../../entities/experiment";
import { t, tFill } from "../../../shared/config";
import { Link } from "../../../shared/lib/router";
import { Button } from "../../../shared/ui";
import "./experiment-completion-notice.css";

/**
 * 실험 완료 알림(검증 랩 V5-01, US-SM-15). 어느 화면에 있든 실험 목록 query(읽은 쪽들)를 구독하고(끝나지
 * 않은 실험이 있으면 목록 query 가 다시 묻는다), 처음 읽었을 때 이미 끝나 있던 실험을 뺀 완료 실험마다
 * "후보 보기"를 띄운다. 완료 판정은 backend 실험 상태 그대로다. 읽지 않은 뒤쪽 실험의 완료는 알리지 않는다.
 */
export const ExperimentCompletionNotice = () => {
  const experiments = useInfiniteQuery(experimentsQuery());
  const [known, setKnown] = useState<ReadonlySet<string> | null>(null);
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(new Set());
  const items = experiments.data?.pages.flatMap((page) => page.items);
  if (known === null && items !== undefined) {
    // 처음 읽은 목록에서 이미 끝난 실험은 알리지 않는다(렌더 중 상태 맞추기).
    setKnown(
      new Set(
        items
          .filter((item) => item.status === "completed")
          .map((item) => item.record.experiment_id),
      ),
    );
  }
  const finished = (items ?? []).filter(
    (item) =>
      item.status === "completed" &&
      known !== null &&
      !known.has(item.record.experiment_id) &&
      !dismissed.has(item.record.experiment_id),
  );
  if (finished.length === 0) return null;
  return (
    <div className="experiment-notice">
      {finished.map((item) => {
        const id = item.record.experiment_id;
        return (
          <div key={id} className="experiment-notice__item" role="status">
            <strong>{tFill("experiments.notice.done", { id })}</strong>{" "}
            <Link
              to="/research/experiments/$experimentId"
              params={{ experimentId: id }}
            >
              {t("experiments.notice.candidates")}
            </Link>{" "}
            <Button
              size="small"
              tone="ghost"
              onClick={() => setDismissed(new Set([...dismissed, id]))}
            >
              {t("experiments.notice.dismiss")}
            </Button>
          </div>
        );
      })}
    </div>
  );
};
