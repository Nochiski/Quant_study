import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import {
  requestRejectionMessage,
  useBacktestRequest,
  type BacktestRunSpec,
} from "../../../entities/backtest";
import {
  experimentQuery,
  useCreateExperiment,
  useExperimentPreview,
  type ExperimentRequest,
} from "../../../entities/experiment";
import { strategyDocumentQuery } from "../../../entities/strategy";
import { failureReason } from "../../../shared/api";
import { t, tFill, type MessageKey } from "../../../shared/config";
import { Link, useNavigate, useSearch } from "../../../shared/lib/router";
import { Button, FailureNotice } from "../../../shared/ui";
import "../../../shared/ui/data-list.css";

// 화면의 분할은 고르는 기준까지 늘 값이 있다. 기준이 빠진 기록은 처음 값(`INITIAL_SPLIT`)의 기준으로 채운다.
type Split = Required<ExperimentRequest["split"]>;
type Search = ExperimentRequest["search"];
type Rule = Split["selection_rule"];

const RULES: Record<Rule, MessageKey> = {
  neighbor_mean_sharpe_max: "experiments.new.rule.neighbor_mean_sharpe_max",
  train_sharpe_max: "experiments.new.rule.train_sharpe_max",
};
const MODES: Record<Split["mode"], MessageKey> = {
  rolling: "experiments.new.mode.rolling",
  anchored: "experiments.new.mode.anchored",
};

// 새 실험 화면의 처음 값(3년 학습 · 1년 검증 · 엠바고 5세션, 디자인보드 예시). 허용 범위는 backend 가 판정한다.
const INITIAL_SPLIT: Split = {
  mode: "rolling",
  train_years: 3,
  test_years: 1,
  embargo_sessions: 5,
  selection_rule: "neighbor_mean_sharpe_max",
};

/** 미리 계산·만들기 거절 문장. 코드 번역은 백테스트 거절과 같은 `backtest.error.<code>` 체계다. */
const rejection = (error: unknown): string =>
  requestRejectionMessage(error, "experiments.new.failed");

const NumberField = ({
  label,
  value,
  onChange,
}: {
  label: MessageKey;
  value: number;
  onChange: (value: number) => void;
}) => {
  const id = useId();
  return (
    <span>
      <label htmlFor={id}>{t(label)}</label>{" "}
      <input
        id={id}
        type="number"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </span>
  );
};

const Form = ({
  base,
  initialSearch,
  initialSplit,
}: {
  base: BacktestRunSpec;
  initialSearch: Search;
  initialSplit: Split;
}) => {
  const navigate = useNavigate();
  const [search, setSearch] = useState(initialSearch);
  const [split, setSplit] = useState(initialSplit);
  const source = base.strategy_source;
  const document = useQuery({
    ...strategyDocumentQuery(
      source?.kind === "saved_revision" ? source.strategy_id : "",
      source?.kind === "saved_revision" ? source.revision : 0,
    ),
    enabled: source?.kind === "saved_revision",
  });
  // 실험의 측정 창은 분할이 정하므로 기반 요청의 지표 창은 싣지 않는다(backend `experiment.base.invalid`).
  const request: ExperimentRequest = {
    run: { ...base, metric_windows: [] },
    search,
    split,
  };
  const preview = useExperimentPreview(request);
  const create = useCreateExperiment();
  const axes = new Map(
    preview.data?.design.search.axes.map((axis) => [
      axis.parameter_id,
      axis.values,
    ]),
  );
  const parameters = document.data?.spec.parameters ?? [];
  return (
    <div className="data-list-page__panel">
      {/* 기반이 무엇인지 한 줄로 보인다 — 어떤 전략·리비전·연구 기간을 나눠 도는지(#402 리뷰 P2-2). */}
      {source?.kind === "saved_revision" && base.environment ? (
        <p>
          {tFill("experiments.new.base", {
            strategy: source.strategy_id,
            revision: source.revision,
            start: base.environment.start,
            end: base.environment.end,
          })}
        </p>
      ) : null}
      <div className="data-list-page__scroll">
        <table className="data-list-page__table">
          <caption>{t("experiments.new.space")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("experiments.new.explore")}</th>
              <th scope="col">{t("experiments.new.parameter")}</th>
              <th scope="col">{t("experiments.new.values")}</th>
            </tr>
          </thead>
          <tbody>
            {parameters.map((parameter) => {
              const id = parameter.parameter_id;
              const values = axes.get(id);
              return (
                <tr key={id}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`${t("experiments.new.explore")}: ${id}`}
                      checked={id in search}
                      onChange={(event) => {
                        const next = { ...search };
                        // 값을 정하지 않으면(null) backend 가 정의의 격자 값 전체를 편다.
                        if (event.target.checked) next[id] = null;
                        else delete next[id];
                        setSearch(next);
                      }}
                    />
                  </td>
                  <td>
                    <code>{id}</code>
                  </td>
                  <td>
                    {values === undefined
                      ? "—"
                      : `${values.join(", ")} (${values.length})`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <fieldset className="data-list-page__filter">
        <legend>{t("experiments.new.split")}</legend>
        <select
          aria-label={t("experiments.new.mode")}
          value={split.mode}
          onChange={(event) =>
            setSplit({ ...split, mode: event.target.value as Split["mode"] })
          }
        >
          {Object.entries(MODES).map(([value, label]) => (
            <option key={value} value={value}>
              {t(label)}
            </option>
          ))}
        </select>
        <NumberField
          label="experiments.new.trainYears"
          value={split.train_years}
          onChange={(value) => setSplit({ ...split, train_years: value })}
        />
        <NumberField
          label="experiments.new.testYears"
          value={split.test_years}
          onChange={(value) => setSplit({ ...split, test_years: value })}
        />
        <NumberField
          label="experiments.new.embargo"
          value={split.embargo_sessions}
          onChange={(value) => setSplit({ ...split, embargo_sessions: value })}
        />
        <select
          aria-label={t("experiments.new.rule")}
          value={split.selection_rule}
          onChange={(event) =>
            setSplit({ ...split, selection_rule: event.target.value as Rule })
          }
        >
          {Object.entries(RULES).map(([value, label]) => (
            <option key={value} value={value}>
              {t(label)}
            </option>
          ))}
        </select>
      </fieldset>
      {preview.isError ? (
        <FailureNotice
          title={t("experiments.new.previewFailed")}
          message={rejection(preview.error)}
          reason={failureReason(preview.error)}
        />
      ) : preview.data === undefined ? (
        <p role="status">{t("page.loading")}</p>
      ) : (
        <>
          <p role="status" aria-label={t("experiments.new.summary")}>
            {tFill("experiments.new.counts", {
              combinations: preview.data.combination_count,
              windows: preview.data.design.windows.length,
              runs: preview.data.run_count,
              before: preview.data.trial_count,
              after: preview.data.trial_count_after,
            })}
          </p>
          <div className="data-list-page__scroll">
            <table className="data-list-page__table">
              <caption>{t("experiments.new.windows")}</caption>
              <thead>
                <tr>
                  <th scope="col">{t("experiments.new.window")}</th>
                  <th scope="col">{t("experiments.new.train")}</th>
                  <th scope="col">{t("experiments.new.test")}</th>
                </tr>
              </thead>
              <tbody>
                {preview.data.design.windows.map((window, index) => (
                  <tr key={index}>
                    <td>{index + 1}</td>
                    <td>
                      {window.train_start} ~ {window.train_end}
                    </td>
                    <td>
                      {window.test_start} ~ {window.test_end}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {create.isError ? (
        <FailureNotice
          title={t("experiments.new.createFailed")}
          message={rejection(create.error)}
          reason={failureReason(create.error)}
        />
      ) : null}
      <span className="data-list-page__actions">
        <Button
          tone="primary"
          disabled={
            !preview.isSuccess || preview.isPlaceholderData || create.isPending
          }
          onClick={() =>
            create.mutate(request, {
              onSuccess: () =>
                void navigate({ to: "/research/experiments", search: {} }),
            })
          }
        >
          {t("experiments.new.enqueue")}
        </Button>
      </span>
    </div>
  );
};

/**
 * 새 실험(검증 랩 V5-01, US-SM-14). 기반은 백테스트 실행(`run`) 하나의 요청이거나, 끝난 실험(`from`)의 기반·
 * 탐색 공간·분할이다. 조합·창·실행 수와 시도 수 변화는 backend 미리 계산이 정한다.
 */
export const NewExperimentPage = () => {
  const { run, from } = useSearch({ from: "/research/experiments/new" });
  const request = useBacktestRequest(run ?? null);
  const origin = useQuery({
    ...experimentQuery(from ?? ""),
    enabled: from !== undefined,
  });
  const source = from !== undefined ? origin : request;
  return (
    <section
      className="page data-list-page"
      aria-labelledby="new-experiment-title"
    >
      <header className="data-list-page__header">
        <div>
          <h1 id="new-experiment-title">{t("experiments.new.title")}</h1>
          <p>{t("experiments.new.description")}</p>
        </div>
      </header>
      {run === undefined && from === undefined ? (
        <p className="page-state">
          {t("experiments.new.noBase")}{" "}
          <Link to="/research/backtests" search={{}}>
            {t("experiments.toBacktests")}
          </Link>
        </p>
      ) : source.isPending ? (
        <p className="page-state" role="status">
          {t("page.loading")}
        </p>
      ) : source.isError ? (
        <FailureNotice
          message={t("experiments.new.baseFailed")}
          reason={failureReason(source.error)}
        />
      ) : origin.data !== undefined ? (
        <Form
          base={origin.data.record.run}
          initialSearch={Object.fromEntries(
            origin.data.record.design.search.axes.map((axis) => [
              axis.parameter_id,
              axis.values,
            ]),
          )}
          initialSplit={{ ...INITIAL_SPLIT, ...origin.data.record.split }}
        />
      ) : request.data !== undefined ? (
        <Form
          base={request.data}
          initialSearch={{}}
          initialSplit={INITIAL_SPLIT}
        />
      ) : null}
    </section>
  );
};
