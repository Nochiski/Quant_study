import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";

import { requestRejectionMessage } from "../../../entities/backtest";
import {
  experimentStatusLabel,
  experimentStatusTone,
  experimentsQuery,
  useCancelExperiment,
  useControlExperiment,
  type Experiment,
} from "../../../entities/experiment";
import { failureReason } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { Link } from "../../../shared/lib/router";
import { Badge, Button, EmptyState, FailureNotice } from "../../../shared/ui";
import "../../../shared/ui/data-list.css";

/** 실험 한 줄의 조작. 대기열 규칙(슬롯·배정·우선순위 상한)은 backend 가 적용한다(spec D6). */
const Actions = ({
  item,
  maxPriority,
}: {
  item: Experiment;
  maxPriority: number;
}) => {
  const [confirming, setConfirming] = useState(false);
  const control = useControlExperiment();
  const cancel = useCancelExperiment();
  const id = item.record.experiment_id;
  const { paused, priority } = item.record.controls;
  const failure = control.error ?? cancel.error;
  return (
    <>
      <span className="data-list-page__actions">
        {item.finished ? null : (
          <>
            <Button
              size="small"
              tone="ghost"
              disabled={control.isPending}
              onClick={() =>
                control.mutate({ experimentId: id, paused: !paused })
              }
            >
              {paused ? t("experiments.resume") : t("experiments.pause")}
            </Button>
            <Button
              size="small"
              tone="ghost"
              disabled={control.isPending || priority >= maxPriority}
              onClick={() =>
                control.mutate({ experimentId: id, priority: priority + 1 })
              }
            >
              {t("experiments.raisePriority")}
            </Button>
            {confirming ? (
              <>
                <Button
                  size="small"
                  tone="danger"
                  disabled={cancel.isPending}
                  onClick={() => cancel.mutate(id)}
                >
                  {t("experiments.cancelConfirm")}
                </Button>
                <Button
                  size="small"
                  tone="ghost"
                  onClick={() => setConfirming(false)}
                >
                  {t("experiments.cancelKeep")}
                </Button>
              </>
            ) : (
              <Button
                size="small"
                tone="ghost"
                onClick={() => setConfirming(true)}
              >
                {t("experiments.cancel")}
              </Button>
            )}
          </>
        )}
        <Link
          className="ui-button ui-button--secondary ui-button--small"
          to="/research/experiments/new"
          search={{ from: id }}
        >
          {t("experiments.again")}
        </Link>
      </span>
      {failure === null ? null : (
        <FailureNotice
          message={requestRejectionMessage(
            failure,
            "experiments.controlFailed",
          )}
          reason={failureReason(failure)}
        />
      )}
    </>
  );
};

export const ExperimentsPage = () => {
  const experiments = useInfiniteQuery(experimentsQuery());
  // 슬롯 사용량·우선순위 상한은 첫 쪽 응답의 값이다(쪽마다 같은 서버 상태를 싣는다).
  const first = experiments.data?.pages[0];
  const items = experiments.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <section
      className="page data-list-page"
      aria-labelledby="experiments-title"
    >
      <header className="data-list-page__header">
        <div>
          <h1 id="experiments-title">{t("experiments.title")}</h1>
          <p>{t("experiments.description")}</p>
        </div>
      </header>
      {experiments.isPending ? (
        <p className="page-state" role="status">
          {t("page.loading")}
        </p>
      ) : experiments.isError || first === undefined ? (
        <div className="page-state page-state--error" role="alert">
          <p>{t("experiments.error")}</p>
          <Button tone="ghost" onClick={() => experiments.refetch()}>
            {t("page.error.retry")}
          </Button>
        </div>
      ) : (
        <div className="data-list-page__panel">
          <p className="data-list-page__filter" role="status">
            {tFill("experiments.slots", first.slots)}
          </p>
          {items.length === 0 ? (
            <EmptyState
              title={t("experiments.emptyTitle")}
              description={t("experiments.empty")}
              action={
                <Link
                  className="ui-button ui-button--primary"
                  to="/research/backtests"
                  search={{}}
                >
                  {t("experiments.toBacktests")}
                </Link>
              }
            />
          ) : (
            <div className="data-list-page__scroll">
              <table className="data-list-page__table">
                <caption className="sr-only">
                  {t("experiments.caption")}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">{t("experiments.experiment")}</th>
                    <th scope="col">{t("experiments.base")}</th>
                    <th scope="col">{t("experiments.status")}</th>
                    <th scope="col">{t("experiments.progress")}</th>
                    <th scope="col">{t("experiments.priority")}</th>
                    <th scope="col">{t("history.actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => {
                    const counts = item.trial_counts;
                    const source = item.record.run.strategy_source;
                    return (
                      <tr key={item.record.experiment_id}>
                        <td>
                          <Link
                            to="/research/experiments/$experimentId"
                            params={{
                              experimentId: item.record.experiment_id,
                            }}
                          >
                            <code>{item.record.experiment_id}</code>
                          </Link>
                        </td>
                        <td>
                          {source?.kind === "saved_revision"
                            ? `${source.strategy_id} · v${source.revision}`
                            : "—"}
                        </td>
                        <td>
                          <Badge tone={experimentStatusTone(item.status)}>
                            {experimentStatusLabel(item.status)}
                          </Badge>
                        </td>
                        <td>
                          {tFill("experiments.counts", {
                            completed: counts.completed ?? 0,
                            total: Object.values(counts).reduce(
                              (sum, count) => sum + (count ?? 0),
                              0,
                            ),
                            running: counts.running ?? 0,
                            failed: counts.failed ?? 0,
                          })}
                        </td>
                        <td>{item.record.controls.priority}</td>
                        <td>
                          <Actions
                            item={item}
                            maxPriority={first.max_priority}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {/* 뒤쪽 실험도 재개·취소할 수 있게 응답의 `next_after` 로 더 읽는다(#402 리뷰 P2-1). */}
          {experiments.hasNextPage ? (
            <Button
              tone="ghost"
              disabled={experiments.isFetchingNextPage}
              onClick={() => void experiments.fetchNextPage()}
            >
              {t("experiments.more")}
            </Button>
          ) : null}
        </div>
      )}
    </section>
  );
};
