import { useId, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { requestRejectionMessage } from "../../../entities/backtest";
import {
  EXPERIMENT_POLL_MS,
  experimentParameterMapQuery,
  noRecommendationLabel,
  trialStatusLabel,
  useSelectExperimentCandidate,
  type Experiment,
  type ExperimentTrialState,
} from "../../../entities/experiment";
import {
  failureReason,
  type CellVerdict,
  type ExperimentSelection,
} from "../../../shared/api";
import { t, tFill, type MessageKey } from "../../../shared/config";
import { displayTime } from "../../../shared/lib/display-time";
import { Link } from "../../../shared/lib/router";
import { Button, FailureNotice } from "../../../shared/ui";
import {
  coordinateKey,
  coordinateLabel,
  initialMapView,
  MAP_PAGE_SIZE,
  visibleAxisIndices,
  mapHeatBand,
  mapHeatScale,
  type HeatMetric,
  type MapView,
} from "../model/parameter-map-view";
import "./experiment-candidate-map.css";
import { ParameterColorLegend } from "./parameter-color-legend";

const VERDICT: Record<CellVerdict, MessageKey> = {
  recommended: "experiments.map.recommended",
  peak: "experiments.map.peak",
  failed: "experiments.map.failed",
  unscored: "experiments.map.unscored",
  scored: "experiments.map.scored",
};
const raw = (value: number | null | undefined) =>
  value == null ? "—" : String(value);
const valuesText = (values: ExperimentSelection["parameter_values"]) =>
  Object.entries(values)
    .map(([name, value]) => `${name} ${String(value)}`)
    .join(" · ");
const runOf = (trial: ExperimentTrialState | undefined) =>
  trial?.attempts.at(-1)?.run_id;

const SelectionRecord = ({
  selection,
  trial,
}: {
  selection: ExperimentSelection;
  trial?: ExperimentTrialState;
}) => {
  const runId = runOf(trial);
  return (
    <article className="candidate-map__record">
      <h4>
        {valuesText(selection.parameter_values) || t("experiments.map.default")}
      </h4>
      <p>{tFill("experiments.map.recordedTrial", { index: selection.trial_index + 1 })}{trial?.trial.window ? ` · ${trial.trial.window.train_start} ~ ${trial.trial.window.train_end}` : ""}</p>
      {runId ? <p>{t("experiments.detail.run")}: <code>{runId}</code></p> : null}
      <p>{selection.reason}</p>
      <dl className="candidate-map__metrics">
        <div>
          <dt>{t("experiments.map.at")}</dt>
          <dd>
            <time dateTime={selection.selected_at}>
              {displayTime(selection.selected_at)}
            </time>
          </dd>
        </div>
        <div>
          <dt>{t("experiments.map.count")}</dt>
          <dd>{raw(selection.trial_count)}</dd>
        </div>
        <div>
          <dt>{t("experiments.map.dsr")}</dt>
          <dd>{raw(selection.deflated_sharpe)}</dd>
        </div>
        <div>
          <dt>{t("experiments.map.representative")}</dt>
          <dd>{raw(selection.ledger_representative_sharpe)}</dd>
        </div>
      </dl>
      {runId ? (
        <Link
          className="ui-button ui-button--secondary"
          to="/research/backtests/$runId"
          params={{ runId }}
        >
          {t("experiments.map.openResult")}
        </Link>
      ) : null}
    </article>
  );
};

/** 서버 판정을 좌표에 놓고 사용자의 명시적 이유만 제출한다. 실험 응답·선택 기록은 query가 소유한다. */
export const ExperimentCandidateMap = ({
  experiment,
  trials,
}: {
  experiment: Experiment;
  trials: ExperimentTrialState[];
}) => {
  const id = experiment.record.experiment_id;
  const axes = experiment.record.design.search.axes;
  const [stored, setView] = useState(() => initialMapView(id, axes));
  // 라우트 신원이 바뀌면 이전 칸·이유는 새 실험의 입력으로 쓰지 않는다.
  const view = stored.owner === id ? stored : initialMapView(id, axes);
  const update = (patch: Partial<MapView>) =>
    setView((current) => ({
      ...(current.owner === id ? current : initialMapView(id, axes)),
      ...patch,
    }));
  const result = useQuery({
    ...experimentParameterMapQuery(id),
    enabled: (query) => query.state.error === null,
    retry: false,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval: (query) =>
      experiment.settled || query.state.error !== null
        ? false
        : EXPERIMENT_POLL_MS,
  });
  const select = useSelectExperimentCandidate(id);
  const heading = useRef<HTMLHeadingElement>(null);
  const grid = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const cells = useMemo(
    () =>
      new Map(
        result.data?.cells.map((cell) => [
          coordinateKey(cell.grid_index),
          cell,
        ]),
      ),
    [result.data],
  );
  const scale = useMemo(
    () => mapHeatScale(result.data?.cells ?? [], view.metric),
    [result.data, view.metric],
  );
  const selected =
    view.selected === null ? undefined : cells.get(view.selected);
  const cellTrials =
    selected === undefined
      ? []
      : trials.filter(
          (trial) =>
            coordinateKey(trial.trial.grid_index) ===
            coordinateKey(selected.grid_index),
        );
  const trial = cellTrials.find(
    (state) => String(state.trial.index) === view.trial,
  );
  const pendingTrials = trials.filter(
    (state) =>
      state.status === "queued" ||
      state.status === "running" ||
      state.awaiting_recovery,
  ).length;
  const success =
    select.data?.experiment_id === id &&
    select.variables?.trial_index === trial?.trial.index
      ? select.data
      : undefined;
  const xAxis = axes[view.x],
    yAxis = axes[view.y];
  const xIndices = visibleAxisIndices(xAxis, view.xPage),
    yIndices = visibleAxisIndices(yAxis, view.yPage);
  const label = (indices: number[]) =>
    coordinateLabel(axes, indices) || t("experiments.map.default");
  const choose = (indices: number[]) => {
    update({
      selected: coordinateKey(indices),
      trial: "",
      reason: "",
      fixed: [...indices],
      xPage: Math.floor((indices[view.x] ?? 0) / MAP_PAGE_SIZE),
      yPage: Math.floor((indices[view.y] ?? 0) / MAP_PAGE_SIZE),
    });
    select.reset();
    heading.current?.focus();
  };
  const pageControls = (
    axis: typeof xAxis,
    direction: "X" | "Y",
    page: number,
  ) =>
    axis === undefined || axis.values.length <= MAP_PAGE_SIZE ? null : (
      <div className="candidate-map__pagination">
        <Button
          size="small"
          disabled={page === 0}
          onClick={() =>
            update(
              direction === "X" ? { xPage: page - 1 } : { yPage: page - 1 },
            )
          }
        >
          {t(`experiments.map.previous${direction}`)}
        </Button>
        <span>
          {tFill("experiments.map.range", {
            start: page * MAP_PAGE_SIZE + 1,
            end: Math.min((page + 1) * MAP_PAGE_SIZE, axis.values.length),
            total: axis.values.length,
          })}
        </span>
        <Button
          size="small"
          disabled={(page + 1) * MAP_PAGE_SIZE >= axis.values.length}
          onClick={() =>
            update(
              direction === "X" ? { xPage: page + 1 } : { yPage: page + 1 },
            )
          }
        >
          {t(`experiments.map.next${direction}`)}
        </Button>
      </div>
    );
  return (
    <section
      className="experiment-section candidate-map"
      aria-labelledby={titleId}
    >
      <h2 id={titleId}>{t("experiments.map.title")}</h2>
      <p>{t("experiments.map.intro")}</p>
      {result.isPending ? (
        <p role="status">{t("experiments.map.loading")}</p>
      ) : result.isError ? (
        <FailureNotice
          message={t("experiments.map.loadFailed")}
          reason={failureReason(result.error)}
        >
          <Button
            onClick={() => {
              void result.refetch();
            }}
          >
            {t("experiments.map.retry")}
          </Button>
        </FailureNotice>
      ) : (
        <>
          {result.data.no_recommendation == null ? null : (
            <p className="experiment-interpretation">
              {noRecommendationLabel(result.data.no_recommendation)}
            </p>
          )}
          <ul
            className="candidate-map__legend"
            aria-label={t("experiments.map.legend")}
          >
            {Object.entries(VERDICT).map(([verdict, key]) => (
              <li key={verdict} data-verdict={verdict}>
                {t(key)}
              </li>
            ))}
          </ul>
          <details className="ui-disclosure candidate-map__explanation">
            <summary>{t("experiments.map.details")}</summary>
            <p>{t("experiments.map.explanation")}</p>
            <p>{t("experiments.map.sensitivityNote")}</p>
          </details>
          {Object.entries(experiment.record.design.parameter_values).some(
            ([name]) => !axes.some((axis) => axis.parameter_id === name),
          ) ? (
            <details className="ui-disclosure">
              <summary>{t("experiments.map.fixed")}</summary>
              <dl className="candidate-map__metrics">
                {Object.entries(experiment.record.design.parameter_values)
                  .filter(
                    ([name]) =>
                      !axes.some((axis) => axis.parameter_id === name),
                  )
                  .map(([name, value]) => (
                    <div key={name}>
                      <dt>{name}</dt>
                      <dd>{String(value)}</dd>
                    </div>
                  ))}
              </dl>
            </details>
          ) : null}
          {axes.length === 0 ? (
            <p>{t("experiments.map.default")}</p>
          ) : axes.length === 1 ? null : (
            <fieldset className="candidate-map__axes">
              <legend>{t("experiments.map.axes")}</legend>
              <label>
                {t("experiments.map.x")}
                <select
                  className="ui-select"
                  value={view.x}
                  onChange={(event) => {
                    const x = Number(event.target.value);
                    update({
                      x,
                      y: x === view.y ? view.x : view.y,
                      xPage: 0,
                      yPage: 0,
                    });
                  }}
                >
                  {axes.map((axis, index) => (
                    <option key={axis.parameter_id} value={index}>
                      {axis.parameter_id}
                    </option>
                  ))}
                </select>
              </label>
              {axes.length > 1 ? (
                <label>
                  {t("experiments.map.y")}
                  <select
                    className="ui-select"
                    value={view.y}
                    onChange={(event) =>
                      update({ y: Number(event.target.value), yPage: 0 })
                    }
                  >
                    <option value={-1}>{t("experiments.map.none")}</option>
                    {axes.map((axis, index) =>
                      index === view.x ? null : (
                        <option key={axis.parameter_id} value={index}>
                          {axis.parameter_id}
                        </option>
                      ),
                    )}
                  </select>
                </label>
              ) : null}
              {axes.map((axis, index) =>
                index === view.x || index === view.y ? null : (
                  <label key={axis.parameter_id}>
                    {axis.parameter_id} · {t("experiments.map.fixed")}
                    <select
                      className="ui-select"
                      value={view.fixed[index] ?? 0}
                      onChange={(event) => {
                        const fixed = [...view.fixed];
                        fixed[index] = Number(event.target.value);
                        update({ fixed });
                      }}
                    >
                      {axis.values.map((value, ordinal) => (
                        <option key={ordinal} value={ordinal}>
                          {String(value)}
                        </option>
                      ))}
                    </select>
                  </label>
                ),
              )}
            </fieldset>
          )}
          {axes.length > 1 ||
          axes.some((axis) => axis.values.length > MAP_PAGE_SIZE) ? (
            <p className="candidate-map__scope">{t("experiments.map.scope")}</p>
          ) : null}
          {pageControls(xAxis, "X", view.xPage)}
          {pageControls(yAxis, "Y", view.yPage)}
          <label className="candidate-map__color-choice">
            {t("experiments.map.colorMetric")}
            <select
              className="ui-select"
              value={view.metric}
              onChange={(event) =>
                update({ metric: event.target.value as HeatMetric })
              }
            >
              <option value="plateau_score">
                {t("experiments.map.plateau")}
              </option>
              <option value="score">{t("experiments.map.score")}</option>
            </select>
          </label>
          <div className="candidate-map__color-overview">
            <span>{scale === null ? "—" : raw(scale.min)}</span>
            <span className="candidate-map__color-strip" aria-hidden="true">
              {Array.from({ length: 7 }, (_, index) => (
                <i
                  key={index}
                  data-heat={
                    scale === null
                      ? "none"
                      : scale.min === scale.max
                        ? 4
                        : index + 1
                  }
                />
              ))}
            </span>
            <span>{scale === null ? "—" : raw(scale.max)}</span>
          </div>
          <ParameterColorLegend scale={scale} />
          <div
            ref={grid}
            className="candidate-map__grid"
            role="region"
            aria-label={t("experiments.map.grid")}
            tabIndex={0}
          >
            <table>
              <caption>
                {xAxis?.parameter_id ?? t("experiments.map.default")}
                {yAxis ? ` × ${yAxis.parameter_id}` : ""}
              </caption>
              <thead>
                <tr>
                  <th scope="col">{yAxis?.parameter_id ?? "—"}</th>
                  {xIndices.map((x) => (
                    <th scope="col" key={x}>
                      {xAxis === undefined ? "—" : String(xAxis.values[x])}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {yIndices.map((y) => (
                  <tr key={y}>
                    <th scope="row">
                      {yAxis === undefined ? "—" : String(yAxis.values[y])}
                    </th>
                    {xIndices.map((x) => {
                      const indices = [...view.fixed];
                      if (view.x >= 0) indices[view.x] = x;
                      if (view.y >= 0) indices[view.y] = y;
                      const key = coordinateKey(indices),
                        cell = cells.get(key);
                      return (
                        <td
                          key={x}
                          data-verdict={cell?.verdict}
                          data-heat={
                            cell?.verdict === "failed" ||
                            cell?.verdict === "unscored"
                              ? "none"
                              : (mapHeatBand(
                                  cell?.[view.metric] ?? null,
                                  scale,
                                ) ?? "none")
                          }
                        >
                          {cell === undefined ? (
                            <span>{t("experiments.map.excluded")}</span>
                          ) : (
                            <Button
                              className="candidate-map__cell"
                              aria-pressed={view.selected === key}
                              aria-label={`${label(indices)} · ${t(VERDICT[cell.verdict])} · ${t(view.metric === "score" ? "experiments.map.score" : "experiments.map.plateau")} ${raw(cell[view.metric])}`}
                              onClick={() => choose(indices)}
                            >
                              <strong>{t(VERDICT[cell.verdict])}</strong>
                              <span>
                                {cell[view.metric] === null
                                  ? "—"
                                  : cell[view.metric]!.toFixed(4)}
                              </span>
                            </Button>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {result.data.cells.length === 0 ? (
            <p>{t("experiments.map.empty")}</p>
          ) : null}
          <section
            className="candidate-map__comparison"
            aria-label={t("experiments.map.compare")}
          >
            <h3 ref={heading} tabIndex={-1}>
              {t("experiments.map.compare")}
            </h3>
            {selected === undefined ? (
              <p>{t("experiments.map.chooseCell")}</p>
            ) : (
              <>
                <p>
                  <strong>{label(selected.grid_index)}</strong> ·{" "}
                  {t(VERDICT[selected.verdict])}
                </p>
                <dl className="candidate-map__metrics">
                  {(
                    [
                      ["score", selected.score],
                      ["plateau", selected.plateau_score],
                      ["sensitivity", selected.sensitivity],
                    ] as const
                  ).map(([key, value]) => (
                    <div key={key}>
                      <dt>{t(`experiments.map.${key}`)}</dt>
                      <dd>{raw(value)}</dd>
                    </div>
                  ))}
                </dl>
                <p>
                  {t("experiments.map.comparison")}:{" "}
                  {selected.sensitivity_cell === null
                    ? "—"
                    : label(selected.sensitivity_cell)}
                </p>
                {selected.sensitivity_cell === null ? null : (
                  <Button onClick={() => choose(selected.sensitivity_cell!)}>
                    {t("experiments.map.openComparison")}
                  </Button>
                )}
                {cellTrials.some((state) => state.bankrupt) ? (
                  <p>{t("experiments.map.bankrupt")}</p>
                ) : null}
                {!cellTrials.some((state) => state.selectable) &&
                pendingTrials > 0 &&
                cellTrials.some((state) => state.status === "completed") ? (
                  <p role="status">
                    {tFill("experiments.map.unfinished", {
                      count: pendingTrials,
                    })}
                  </p>
                ) : !cellTrials.some((state) => state.selectable) ? (
                  <p>{t("experiments.map.notSelectable")}</p>
                ) : success !== undefined ? null : (
                  <form
                    aria-label={t("experiments.map.form")}
                    onSubmit={(event) => {
                      event.preventDefault();
                      if (
                        !trial?.selectable ||
                        view.reason.trim() === "" ||
                        select.isPending
                      )
                        return;
                      select.mutate({
                        trial_index: trial.trial.index,
                        reason: view.reason,
                      });
                    }}
                  >
                    <p>{t("experiments.map.irreversible")}</p>
                    <label>
                      {t("experiments.map.trial")}
                      <select
                        className="ui-select"
                        required
                        value={view.trial}
                        onChange={(event) => {
                          update({ trial: event.target.value });
                          select.reset();
                        }}
                      >
                        <option value="">
                          {t("experiments.map.chooseTrial")}
                        </option>
                        {cellTrials.map((state) => (
                          <option
                            key={state.trial.index}
                            value={state.trial.index}
                            disabled={!state.selectable}
                          >
                            #{state.trial.index + 1} ·{" "}
                            {state.trial.window === null
                              ? "—"
                              : `${state.trial.window.train_start} ~ ${state.trial.window.train_end}`}{" "}
                            · {trialStatusLabel(state.status)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      {t("experiments.map.reason")}
                      <textarea
                        className="ui-input"
                        required
                        value={view.reason}
                        onChange={(event) =>
                          update({ reason: event.target.value })
                        }
                      />
                    </label>
                    <Button
                      type="submit"
                      tone="primary"
                      disabled={
                        !trial?.selectable ||
                        view.reason.trim() === "" ||
                        select.isPending ||
                        success !== undefined
                      }
                    >
                      {t(
                        select.isPending
                          ? "experiments.map.saving"
                          : "experiments.map.submit",
                      )}
                    </Button>
                  </form>
                )}
                {select.isError ? (
                  <FailureNotice
                    message={requestRejectionMessage(
                      select.error,
                      "experiments.map.failedRequest",
                    )}
                    reason={failureReason(select.error)}
                  />
                ) : null}
                {success === undefined ? null : (
                  <div>
                    <p role="status">{t("experiments.map.recorded")}</p>
                    <p>{t("experiments.map.snapshot")}</p>
                    <SelectionRecord selection={success} trial={trial} />
                  </div>
                )}
                <Button tone="ghost" onClick={() => grid.current?.focus()}>
                  {t("experiments.map.backToMap")}
                </Button>
              </>
            )}
          </section>
        </>
      )}
      {experiment.selections.length === 0 ? null : (
        <details className="ui-disclosure candidate-map__history">
          <summary>
            {t("experiments.map.history")} ({experiment.selections.length})
          </summary>
          <p>{t("experiments.map.snapshot")}</p>
          <p>{t("experiments.map.representativeNote")}</p>
          {experiment.selections.map((selection, index) => (
            <SelectionRecord
              key={`${selection.selected_at}-${index}`}
              selection={selection}
              trial={trials.find(
                (state) => state.trial.index === selection.trial_index,
              )}
            />
          ))}
        </details>
      )}
    </section>
  );
};
