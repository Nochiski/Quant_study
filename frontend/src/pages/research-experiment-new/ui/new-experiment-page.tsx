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
import {
  strategyDocumentQuery,
  type StrategyDocument,
} from "../../../entities/strategy";
import { failureReason } from "../../../shared/api";
import { t, tDuration, tFill, type MessageKey } from "../../../shared/config";
import { Link, useNavigate, useSearch } from "../../../shared/lib/router";
import { Button, FailureNotice } from "../../../shared/ui";
import "../../../shared/ui/data-list.css";
import { ConstraintEditor, type Constraint } from "./constraint-editor";

// 화면의 분할은 고르는 기준까지 늘 값이 있다. 기준이 빠진 기록은 처음 값(`INITIAL_SPLIT`)의 기준으로 채운다.
type Split = Required<ExperimentRequest["split"]>;
type Search = ExperimentRequest["search"];
type SearchValue = NonNullable<Search[string]>[number];
type Parameter = NonNullable<StrategyDocument["spec"]["parameters"]>[number];
// 탐색하는 파라미터의 값 입력. 숫자 파라미터를 고쳐 적는 중이면 입력 원문, 선택지에서 고른 값·원 실험에서 받은
// 값은 값 배열이다 — 받은 값을 글자로 왕복하지 않아 쉼표가 든 선택지도 그대로 다시 실린다(#518 리뷰 N1).
type Entry = string | SearchValue[];
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

/**
 * 값 입력 글자 하나를 요청 값으로 옮긴다. 숫자 파라미터는 숫자로, 선택지는 같은 글자의 선택지 값으로 읽고,
 * 읽지 못한 글자는 그대로 보내 허용 판정(`experiment.search.invalid_values`·`invalid_constraint`)을
 * backend 에 맡긴다. 탐색 값과 제약 상수가 같은 규칙으로 읽힌다.
 */
const parseValue = (
  token: string,
  parameter: Parameter | undefined,
): SearchValue => {
  if (parameter?.kind === "choice")
    return (
      parameter.choices?.find((choice) => String(choice) === token) ?? token
    );
  const number = Number(token);
  return token !== "" && Number.isFinite(number) ? number : token;
};

/**
 * 값 입력을 요청 값으로 옮긴다. 원문은 쉼표로 나눠 숫자로 읽는다. 비면 null — backend 가 정의의 격자 값
 * 전체를 편다.
 */
const searchValues = (entry: Entry): SearchValue[] | null => {
  const values =
    typeof entry === "string"
      ? entry
          .split(",")
          .map((token) => token.trim())
          .filter((token) => token !== "")
          .map((token) => parseValue(token, undefined))
      : entry;
  return values.length === 0 ? null : values;
};

/** 탐색 값 입력 칸. 선택지는 목록에서 고르고(글자로 적지 않는다), 숫자는 쉼표로 적는다. */
const ValuesInput = ({
  parameter,
  entry,
  onChange,
}: {
  parameter: Parameter;
  entry: Entry;
  onChange: (entry: Entry) => void;
}) => {
  const label = `${t("experiments.new.valuesInput")}: ${parameter.parameter_id}`;
  if (parameter.kind === "choice") {
    const picked = typeof entry === "string" ? [] : entry;
    return (
      <select className="ui-select"
        multiple
        aria-label={label}
        value={parameter.choices.flatMap((choice, index) =>
          picked.includes(choice) ? [String(index)] : [],
        )}
        onChange={(event) =>
          onChange(
            Array.from(
              event.target.selectedOptions,
              (option) => parameter.choices[Number(option.value)]!,
            ),
          )
        }
      >
        {parameter.choices.map((choice, index) => (
          <option key={index} value={index}>
            {String(choice)}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input className="ui-input"
      aria-label={label}
      placeholder={t("experiments.new.valuesPlaceholder")}
      value={typeof entry === "string" ? entry : entry.join(", ")}
      onChange={(event) => onChange(event.target.value)}
    />
  );
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
      <input className="ui-input"
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
  initialConstraints = [],
}: {
  base: BacktestRunSpec;
  initialSearch: Search;
  initialSplit: Split;
  initialConstraints?: Constraint[];
}) => {
  const navigate = useNavigate();
  // 탐색하는 파라미터 → 값 입력. 원 실험의 값은 받은 그대로 둔다.
  const [entries, setEntries] = useState<Record<string, Entry>>(() =>
    Object.fromEntries(
      Object.entries(initialSearch).map(([id, values]) => [id, values ?? []]),
    ),
  );
  const [constraints, setConstraints] =
    useState<Constraint[]>(initialConstraints);
  const [split, setSplit] = useState(initialSplit);
  const source = base.strategy_source;
  const document = useQuery({
    ...strategyDocumentQuery(
      source?.kind === "saved_revision" ? source.strategy_id : "",
      source?.kind === "saved_revision" ? source.revision : 0,
    ),
    enabled: source?.kind === "saved_revision",
  });
  const parameters = document.data?.spec.parameters ?? [];
  const definitions = new Map<string, Parameter>(
    parameters.map((parameter) => [parameter.parameter_id, parameter]),
  );
  const search: Search = Object.fromEntries(
    Object.entries(entries).map(([id, entry]) => [id, searchValues(entry)]),
  );
  const explore = (id: string, on: boolean) => {
    const next = { ...entries };
    if (on) next[id] = [];
    else {
      delete next[id];
      // 탐색을 끈 파라미터를 가리키는 제약은 함께 뺀다(남기면 backend 가 거절한다).
      setConstraints(
        constraints.filter(
          (constraint) =>
            constraint.left !== id && constraint.right_parameter !== id,
        ),
      );
    }
    setEntries(next);
  };
  // 실험의 측정 창은 분할이 정하므로 기반 요청의 지표 창은 싣지 않는다(backend `experiment.base.invalid`).
  // 제약은 있을 때만 싣는다 — 비어 있으면 곱집합 전체라 요청 모양을 바꾸지 않는다.
  const request: ExperimentRequest = {
    run: { ...base, metric_windows: [] },
    search,
    split,
    ...(constraints.length > 0
      ? {
          // 상수는 입력 원문으로 들고 있다가(소수점 입력 중 "1." 이 1 로 접히지 않게) 왼쪽 파라미터 규칙으로 읽는다.
          constraints: constraints.map((constraint) =>
            constraint.right_parameter == null
              ? {
                  ...constraint,
                  right_value: parseValue(
                    String(constraint.right_value ?? "").trim(),
                    definitions.get(constraint.left),
                  ),
                }
              : constraint,
          ),
        }
      : {}),
  };
  const preview = useExperimentPreview(request);
  const create = useCreateExperiment();
  const axes = new Map(
    preview.data?.design.search.axes.map((axis) => [
      axis.parameter_id,
      axis.values,
    ]),
  );
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
              <th scope="col">{t("experiments.new.valuesInput")}</th>
            </tr>
          </thead>
          <tbody>
            {parameters.map((parameter) => {
              const id = parameter.parameter_id;
              const values = axes.get(id);
              const entry = id in entries ? entries[id] : undefined;
              return (
                <tr key={id}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`${t("experiments.new.explore")}: ${id}`}
                      checked={id in entries}
                      onChange={(event) => explore(id, event.target.checked)}
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
                  <td>
                    {entry === undefined ? null : (
                      <ValuesInput
                        parameter={parameter}
                        entry={entry}
                        onChange={(next) =>
                          setEntries({ ...entries, [id]: next })
                        }
                      />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <ConstraintEditor
        explored={Object.keys(entries)}
        constraints={constraints}
        onChange={setConstraints}
      />
      <fieldset className="data-list-page__filter">
        <legend>{t("experiments.new.split")}</legend>
        <select className="ui-select"
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
        <select className="ui-select"
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
          {/* 어림은 backend 가 계열의 최근 완료 실행으로 낸다(로드맵 M6). 화면은 근거와 함께 그대로 보인다. */}
          <p>
            {preview.data.time_estimate
              ? tFill("experiments.new.estimate", {
                  total: tDuration(preview.data.time_estimate.total_seconds),
                  samples: preview.data.time_estimate.sample_count,
                })
              : t("experiments.new.estimateUnknown")}
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
          initialConstraints={
            origin.data.record.design.search.constraints ?? []
          }
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
