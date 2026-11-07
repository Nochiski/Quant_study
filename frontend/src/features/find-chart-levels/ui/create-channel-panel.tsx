import { useId, useState } from "react";

import {
  formatPrice,
  regimeVerdictLabel,
  structureLabel,
  timeframeLabel,
  trendDirectionLabel,
  type ChartInstrumentBody,
  type ChartTarget,
  type Timeframe,
  type TrendDirection,
} from "../../../entities/chart";
import { t, tFill } from "../../../shared/config";
import { Badge, Button } from "../../../shared/ui";
import type { DirectionChoice } from "../model/finder-panel";
import { useAddCandidates } from "../model/use-add-candidates";
import type { LevelFinder } from "../model/use-level-finder";
import { serverParam } from "../model/line-evidence";
import { FinderFailure, ParamField, WindowFields } from "./finder-fields";
import { FinderPanelFrame } from "./finder-panel-frame";

type CreateChannelPanelProps = {
  finder: LevelFinder;
  target: ChartTarget;
  timeframe: Timeframe;
  instrument: ChartInstrumentBody;
};

const DIRECTION_CHOICES: readonly (TrendDirection | "none")[] = [
  "up",
  "down",
  "none",
];

const signed = (value: number) =>
  `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(2)}`;

/**
 * 피보나치 채널 만들기: 판정 구간(날짜·자동 제안 칩, 차트 위 음영 테두리는 위젯이 그린다) → 국면 판정(서버 판정·문장
 * 그대로) → 그릴 채널(추천 표시, 추천과 다르면 경고 — 방향 출처 `chosen_by`는 응답 기록이 정한다) → 기준점(세 점 자동
 * 또는 판정 응답의 확정 피벗 목록에서 첫 점) → 파라미터 → 후보 → 차트에 추가. 미리보기는 메인 차트다.
 */
export const CreateChannelPanel = ({
  finder,
  target,
  timeframe,
  instrument,
}: CreateChannelPanelProps) => {
  const id = useId();
  const { panel, dispatch, results } = finder;
  const { regime, channels, direction, recommended } = results;
  const adding = useAddCandidates(target, timeframe);
  const [firstMode, setFirstMode] = useState(false);
  if (panel.view !== "create") return null;
  const { draft } = panel;
  const close = () => dispatch({ type: "close" });
  const judged = regime.data;
  const found = direction === null ? undefined : channels.data;
  const record = found?.record;
  const pivotKind = direction === "down" ? "high" : "low";
  const pivots = (judged?.pivots ?? []).filter(
    (pivot) => pivot.kind === pivotKind,
  );
  const picked = found?.candidates[draft.candidate];
  const price = (value: number) => formatPrice(value, instrument.kind);
  const usingFirst = firstMode || draft.firstAnchor !== null;
  const overridden = record?.chosen_by === "user_override";
  const changedParams =
    draft.candidateSensitivity !== null || draft.structureSensitivity !== null;

  const add = async () => {
    if (picked === undefined || record === undefined) return;
    const preview = results.previews.find((item) => item.key === "channel");
    if (preview !== undefined && (await adding.add([preview], record))) close();
  };

  return (
    <FinderPanelFrame
      title={t("chart.finder.channel.title")}
      subtitle={tFill("chart.finder.previewing", {
        name: instrument.name,
        timeframe: timeframeLabel(timeframe),
      })}
      onClose={close}
      footer={
        <>
          <Button
            tone="primary"
            disabled={picked === undefined || adding.pending}
            onClick={() => void add()}
          >
            {t("chart.finder.add")}
          </Button>
          <Button onClick={close}>{t("chart.finder.cancel")}</Button>
        </>
      }
    >
      <WindowFields
        window={results.window}
        suggestions={judged?.suggestions}
        onChange={(window) => dispatch({ type: "edit", input: { window } })}
        note={tFill("chart.finder.channel.windowNote", {
          date: judged?.context.as_of ?? "",
        })}
      />
      {(judged?.warnings ?? []).map((warning) => (
        <p key={warning} className="finder-warning">
          <Badge tone="warn">{t("chart.finder.caution")}</Badge> {warning}
        </p>
      ))}

      <section className="finder-section" aria-labelledby={`${id}-regime`}>
        <h3 id={`${id}-regime`}>
          {t("chart.finder.regime.title")}
          {judged ? (
            <span className="finder-section__meta">
              {tFill("chart.finder.regime.bars", {
                timeframe: timeframeLabel(timeframe),
                bars: judged.window.bars,
              })}
            </span>
          ) : null}
        </h3>
        {regime.isError ? <FinderFailure error={regime.error} /> : null}
        {judged === undefined ? (
          regime.isPending ? (
            <p className="finder-note" role="status">
              {t("chart.finder.loading")}
            </p>
          ) : null
        ) : (
          <>
            <div className="finder-verdict" role="status">
              <strong>{regimeVerdictLabel(judged.verdict)}</strong>
              <p>{judged.sentence}</p>
            </div>
            <table className="finder-table">
              <thead>
                <tr>
                  <th scope="col">{t("chart.finder.regime.measure")}</th>
                  <th scope="col">{t("chart.finder.regime.value")}</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row">{t("chart.finder.regime.strength")}</th>
                  <td>
                    {judged.strength === null ? "—" : signed(judged.strength)}
                  </td>
                </tr>
                <tr>
                  <th scope="row">{t("chart.finder.regime.structure")}</th>
                  <td>{structureLabel(judged.structure)}</td>
                </tr>
                <tr>
                  <th scope="row">{t("chart.finder.regime.box")}</th>
                  <td>
                    {judged.box_ratio === null
                      ? "—"
                      : judged.box_ratio.toFixed(2)}
                  </td>
                </tr>
              </tbody>
            </table>
            <details className="finder-details">
              <summary>{t("chart.finder.regime.rules")}</summary>
              <ul>
                {judged.rules.map((rule) => (
                  <li key={rule}>{rule}</li>
                ))}
              </ul>
            </details>
          </>
        )}
      </section>

      <fieldset className="finder-section finder-choices">
        <legend>{t("chart.finder.channel.direction")}</legend>
        {DIRECTION_CHOICES.map((choice) => (
          <label key={choice} className="finder-choice">
            <input
              type="radio"
              name={`${id}-direction`}
              checked={(direction ?? "none") === choice}
              onChange={() =>
                dispatch({
                  type: "edit",
                  input: { direction: choice satisfies DirectionChoice },
                })
              }
            />
            {choice === "none"
              ? t("chart.finder.channel.none")
              : trendDirectionLabel(choice)}
            {choice !== "none" && choice === recommended ? (
              <Badge tone="info">{t("chart.finder.recommended")}</Badge>
            ) : null}
          </label>
        ))}
        {judged !== undefined && recommended === null ? (
          <p className="finder-note">
            {t("chart.finder.channel.noRecommendation")}
            {judged.verdict === "box" ? (
              <>
                {" "}
                <Button
                  size="small"
                  onClick={() =>
                    dispatch({
                      type: "openCreate",
                      tool: "hline",
                      seed: { window: results.window },
                      returnKey: panel.returnKey,
                    })
                  }
                >
                  {t("chart.finder.channel.toZones")}
                </Button>
              </>
            ) : null}
          </p>
        ) : null}
        {overridden ? (
          <p className="finder-warning">
            <Badge tone="warn">{t("chart.finder.caution")}</Badge>{" "}
            {tFill("chart.finder.channel.override", {
              recommended:
                recommended === null ? "" : trendDirectionLabel(recommended),
            })}
          </p>
        ) : null}
      </fieldset>

      {direction === null ? null : (
        <fieldset className="finder-section finder-choices">
          <legend>{t("chart.finder.anchor.title")}</legend>
          <label className="finder-choice">
            <input
              type="radio"
              name={`${id}-anchor`}
              checked={!usingFirst}
              onChange={() => {
                setFirstMode(false);
                dispatch({ type: "edit", input: { firstAnchor: null } });
              }}
            />
            {t("chart.finder.anchor.auto")}
          </label>
          <label className="finder-choice">
            <input
              type="radio"
              name={`${id}-anchor`}
              checked={usingFirst}
              onChange={() => setFirstMode(true)}
            />
            {t("chart.finder.anchor.first")}
          </label>
          {usingFirst ? (
            <div className="finder-field">
              <label htmlFor={`${id}-first`}>
                {tFill("chart.finder.anchor.pick", {
                  kind: t(`chart.finder.pivot.${pivotKind}`),
                })}
              </label>
              <select
                id={`${id}-first`}
                className="ui-select"
                value={draft.firstAnchor ?? ""}
                onChange={(event) =>
                  dispatch({
                    type: "edit",
                    input: {
                      firstAnchor:
                        event.target.value === "" ? null : event.target.value,
                    },
                  })
                }
              >
                <option value="">{t("chart.finder.anchor.placeholder")}</option>
                {pivots.map((pivot) => (
                  <option key={pivot.bar_date} value={pivot.bar_date}>
                    {tFill("chart.finder.anchor.option", {
                      date: pivot.bar_date,
                      price: price(pivot.price),
                      confirmed: pivot.confirmed_date,
                    })}
                  </option>
                ))}
              </select>
              <p className="finder-note">{t("chart.finder.anchor.note")}</p>
            </div>
          ) : null}
          {found?.anchor_issue_message ? (
            <p className="finder-warning">{found.anchor_issue_message}</p>
          ) : null}
        </fieldset>
      )}

      <details className="finder-section finder-details">
        <summary>
          {t("chart.finder.params.title")} ·{" "}
          {changedParams
            ? t("chart.finder.params.changed")
            : t("chart.finder.params.default")}
        </summary>
        <ParamField
          label={t("chart.finder.params.candidate")}
          value={draft.candidateSensitivity}
          serverValue={serverParam(record?.params, "candidate_sensitivity")}
          step={0.1}
          onCommit={(value) =>
            dispatch({ type: "edit", input: { candidateSensitivity: value } })
          }
        />
        <ParamField
          label={t("chart.finder.params.structure")}
          value={draft.structureSensitivity}
          serverValue={serverParam(record?.params, "structure_sensitivity")}
          step={0.1}
          onCommit={(value) =>
            dispatch({ type: "edit", input: { structureSensitivity: value } })
          }
        />
        <p className="finder-note">{t("chart.finder.params.pivotNote")}</p>
      </details>

      {direction === null ? null : (
        <fieldset className="finder-section finder-choices">
          <legend>
            {t("chart.finder.channel.candidates")}
            {found ? (
              <span className="finder-section__meta">
                {tFill("chart.finder.channel.count", {
                  shown: found.candidates.length,
                  total: found.total_candidates,
                })}
              </span>
            ) : null}
          </legend>
          {channels.isError ? <FinderFailure error={channels.error} /> : null}
          {found !== undefined &&
          found.candidates.length === 0 &&
          found.anchor_issue_message === null ? (
            <p className="finder-note">{t("chart.finder.channel.empty")}</p>
          ) : null}
          {(found?.candidates ?? []).map((candidate, index) => {
            const [a, b] = candidate.drawing.anchors;
            return (
              <label
                key={`${a.bar_date}-${b.bar_date}-${index}`}
                className="finder-choice finder-choice--candidate"
              >
                <input
                  type="radio"
                  name={`${id}-candidate`}
                  checked={draft.candidate === index}
                  onChange={() => dispatch({ type: "pickCandidate", index })}
                />
                <span>
                  <strong>
                    {tFill("chart.finder.channel.candidate", {
                      rank: index + 1,
                      from: a.bar_date,
                      to: b.bar_date,
                    })}
                  </strong>
                  <span className="finder-choice__meta">
                    {tFill("chart.finder.channel.meta", {
                      outside: (candidate.outside_ratio * 100).toFixed(1),
                      width: candidate.width_atr.toFixed(1),
                      created: candidate.created,
                    })}
                  </span>
                </span>
              </label>
            );
          })}
        </fieldset>
      )}
      {adding.error ? <FinderFailure error={adding.error} /> : null}
    </FinderPanelFrame>
  );
};
