import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { requestRejectionMessage } from "../../../entities/backtest";
import {
  experimentStatusLabel,
  experimentStatusTone,
  experimentsQuery,
  isExperimentSettled,
  useCancelExperiment,
  useControlExperiment,
  type Experiment,
} from "../../../entities/experiment";
import { failureReason } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { Link, useSearch } from "../../../shared/lib/router";
import { Badge, Button, EmptyState, FailureNotice } from "../../../shared/ui";
import "../../../shared/ui/data-list.css";

/** 실험 조작 값. backend 는 늘 싣지만 스키마에서 기본값 칸이라 선택 칸이다 — 폴백은 여기 한 곳이다. */
const controlsOf = (item: Experiment) => ({
  paused: item.record.controls?.paused ?? false,
  priority: item.record.controls?.priority ?? 1,
});

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
  const { paused, priority } = controlsOf(item);
  const failure = control.error ?? cancel.error;
  return (
    <>
      <span className="data-list-page__actions">
        {isExperimentSettled(item.status) ? null : (
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
  const { after } = useSearch({ from: "/research/experiments" });
  const experiments = useQuery(experimentsQuery(after));
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
      ) : experiments.isError ? (
        <div className="page-state page-state--error" role="alert">
          <p>{t("experiments.error")}</p>
          <Button tone="ghost" onClick={() => experiments.refetch()}>
            {t("page.error.retry")}
          </Button>
        </div>
      ) : (
        <div className="data-list-page__panel">
          <p className="data-list-page__filter" role="status">
            {tFill("experiments.slots", experiments.data.slots)}
          </p>
          {experiments.data.items.length === 0 ? (
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
                  {experiments.data.items.map((item) => {
                    const counts = item.trial_counts;
                    const source = item.record.run.strategy_source;
                    return (
                      <tr key={item.record.experiment_id}>
                        <td>
                          <code>{item.record.experiment_id}</code>
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
                        <td>{controlsOf(item).priority}</td>
                        <td>
                          <Actions
                            item={item}
                            maxPriority={experiments.data.max_priority}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {/* 목록은 커서 쪽 넘김이다(응답의 `next_after`). 뒤쪽 실험도 재개·취소할 수 있어야 한다(#402 리뷰 P2-1). */}
          {after === undefined && experiments.data.next_after == null ? null : (
            <nav
              className="data-list-page__actions"
              aria-label={t("experiments.pagination")}
            >
              {after === undefined ? null : (
                <Link to="/research/experiments" search={{}}>
                  {t("experiments.firstPage")}
                </Link>
              )}
              {experiments.data.next_after == null ? null : (
                <Link
                  to="/research/experiments"
                  search={{ after: experiments.data.next_after }}
                >
                  {t("history.next")}
                </Link>
              )}
            </nav>
          )}
        </div>
      )}
    </section>
  );
};
