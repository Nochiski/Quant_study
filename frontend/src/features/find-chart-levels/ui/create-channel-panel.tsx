import { useId } from "react";

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
import { strengthText } from "../model/line-evidence";
import { useAddCandidates } from "../model/use-add-candidates";
import type { LevelFinder } from "../model/use-level-finder";
import { CreateFooter, FinderFailure, ParamField, RefreshingNote, WindowFields } from "./finder-fields";
import { FinderPanelFrame } from "./finder-panel-frame";

type CreateChannelPanelProps = {
  finder: LevelFinder;
  target: ChartTarget;
  timeframe: Timeframe;
  instrument: ChartInstrumentBody;
};

const DIRECTION_CHOICES: readonly (TrendDirection | "none")[] = ["up", "down", "none"];

/**
 * 피보나치 채널 만들기: 판정 구간(날짜·자동 제안 칩, 차트 위 음영 테두리는 위젯이 그린다) → 국면 판정(서버 판정·문장
 * 그대로) → 그릴 채널(추천 표시, 추천과 다르면 경고 — 방향 출처 `chosen_by`는 응답 기록이 정한다) → 기준점(세 점 자동
 * 또는 채널 응답의 첫 점 후보 `anchor_choices`) → 파라미터 → 후보 → 차트에 추가. 미리보기는 메인 차트다.
 *
 * 새 판정·후보가 오기 전(앞 응답을 잠깐 보이는 동안)에는 방향 라디오를 비우고 후보 고르기·추가를 막는다.
 */
export const CreateChannelPanel = ({ finder, target, timeframe, instrument }: CreateChannelPanelProps) => {
  const id = useId();
  const { panel, dispatch, results } = finder;
  const { regime, judged, channels, direction, recommended, ready } = results;
  const adding = useAddCandidates(target, timeframe);
  if (panel.view !== "create") return null;
  const { draft } = panel;
  const close = () => dispatch({ type: "close" });
  // 판정 응답(앞 구간의 것이라도)은 표를 깜박이지 않게 보이되, 방향·후보는 지금 구간의 판정에서만 고른다
  const shownRegime = regime.data;
  const found = direction === null ? undefined : channels.data;
  const record = found?.record;
  const picked = ready ? found?.candidates[draft.candidate] : undefined;
  const price = (value: number) => formatPrice(value, instrument.kind);
  const overridden = ready && record?.chosen_by === "user_override";

  const add = async () => {
    const preview = results.previews.find((item) => item.key === "channel");
    if (picked === undefined || record === undefined || preview === undefined) return;
    if ((await adding.add([preview], record))?.failed === 0) close();
  };

  return (
    <FinderPanelFrame
      title={t("chart.finder.channel.title")}
      subtitle={tFill("chart.finder.previewing", { name: instrument.name, timeframe: timeframeLabel(timeframe) })}
      onClose={close}
      footer={
        <CreateFooter
          addLabel={t("chart.finder.add")}
          canAdd={picked !== undefined}
          onAdd={() => void add()}
          onClose={close}
          adding={adding}
        />
      }
    >
      <WindowFields
        window={results.window}
        suggestions={shownRegime?.suggestions}
        onChange={(window) => dispatch({ type: "edit", input: { window } })}
        note={tFill("chart.finder.channel.windowNote", { date: shownRegime?.context.as_of ?? "" })}
      />
      {(judged?.warnings ?? []).map((warning) => (
        <p key={warning} className="finder-warning">
          <Badge tone="warn">{t("chart.finder.caution")}</Badge> {warning}
        </p>
      ))}

      <RefreshingNote refreshing={results.refreshing} />
      <section
        className={`finder-section${results.refreshing ? " finder-section--stale" : ""}`}
        aria-labelledby={`${id}-regime`}
        aria-busy={judged === undefined}
      >
        <h3 id={`${id}-regime`}>
          {t("chart.finder.regime.title")}
          {shownRegime ? (
            <span className="finder-section__meta">
              {tFill("chart.finder.regime.bars", { timeframe: timeframeLabel(timeframe), bars: shownRegime.window.bars })}
            </span>
          ) : null}
        </h3>
        {regime.isError ? <FinderFailure error={regime.error} /> : null}
        {shownRegime === undefined ? (
          regime.isPending ? (
            <p className="finder-note" role="status">
              {t("chart.finder.loading")}
            </p>
          ) : null
        ) : (
          <>
            <div className="finder-verdict" role="status">
              <strong>{regimeVerdictLabel(shownRegime.verdict)}</strong>
              <p>{shownRegime.sentence}</p>
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
                  <td>{strengthText(shownRegime.strength)}</td>
                </tr>
                <tr>
                  <th scope="row">{t("chart.finder.regime.structure")}</th>
                  <td>{structureLabel(shownRegime.structure)}</td>
                </tr>
                <tr>
                  <th scope="row">{t("chart.finder.regime.box")}</th>
                  <td>{shownRegime.box_ratio === null ? "—" : shownRegime.box_ratio.toFixed(2)}</td>
                </tr>
              </tbody>
            </table>
            <details className="finder-details">
              <summary>{t("chart.finder.regime.rules")}</summary>
              <ul>
                {shownRegime.rules.map((rule) => (
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
              // 지금 구간의 판정이 오기 전에는 비워 둔다(앞 구간의 추천을 보이지 않는다)
              checked={judged !== undefined && (direction ?? "none") === choice}
              onChange={() => dispatch({ type: "edit", input: { direction: choice } })}
            />
            {choice === "none" ? t("chart.finder.channel.none") : trendDirectionLabel(choice)}
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
                    dispatch({ type: "openCreate", tool: "hline", seed: { window: results.window }, returnKey: panel.returnKey })
                  }
                >
                  {t("chart.finder.channel.toZones")}
                </Button>
              </>
            ) : null}
          </p>
        ) : null}
        {overridden && recommended !== null ? (
          <p className="finder-warning">
            <Badge tone="warn">{t("chart.finder.caution")}</Badge>{" "}
            {tFill("chart.finder.channel.override", { recommended: trendDirectionLabel(recommended) })}
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
              checked={draft.anchorMode === "auto"}
              onChange={() => dispatch({ type: "edit", input: { anchorMode: "auto" } })}
            />
            {t("chart.finder.anchor.auto")}
          </label>
          <label className="finder-choice">
            <input
              type="radio"
              name={`${id}-anchor`}
              checked={draft.anchorMode === "first"}
              onChange={() => dispatch({ type: "edit", input: { anchorMode: "first" } })}
            />
            {t("chart.finder.anchor.first")}
          </label>
          {draft.anchorMode === "first" ? (
            <div className="finder-field">
              <label htmlFor={`${id}-first`}>
                {t("chart.finder.anchor.pick")}
              </label>
              <select
                id={`${id}-first`}
                className="ui-select"
                value={draft.firstAnchor ?? ""}
                disabled={found === undefined}
                onChange={(event) =>
                  dispatch({ type: "edit", input: { firstAnchor: event.target.value === "" ? null : event.target.value } })
                }
              >
                <option value="">{t("chart.finder.anchor.placeholder")}</option>
                {(found?.anchor_choices ?? []).map((pivot) => (
                  <option key={pivot.bar_date} value={pivot.bar_date}>
                    {tFill("chart.finder.anchor.option", {
                      kind: t(`chart.finder.pivot.${pivot.kind}`),
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
          {found?.anchor_issue_message ? <p className="finder-warning">{found.anchor_issue_message}</p> : null}
        </fieldset>
      )}

      <details className="finder-section finder-details">
        <summary>
          {t("chart.finder.params.title")} ·{" "}
          {draft.paramsTouched ? t("chart.finder.params.changed") : t("chart.finder.params.default")}
        </summary>
        <ParamField
          label={t("chart.finder.params.candidate")}
          value={draft.candidateSensitivity}
          serverValue={(record?.params.candidate_sensitivity ?? null)}
          step={0.1}
          onCommit={(value) => dispatch({ type: "edit", input: { candidateSensitivity: value } })}
        />
        <ParamField
          label={t("chart.finder.params.structure")}
          value={draft.structureSensitivity}
          serverValue={(record?.params.structure_sensitivity ?? null)}
          step={0.1}
          onCommit={(value) => dispatch({ type: "edit", input: { structureSensitivity: value } })}
        />
        {shownRegime ? <p className="finder-note">{shownRegime.pivot_rule}</p> : null}
      </details>

      {direction === null ? null : (
        <fieldset
          className={`finder-section finder-choices${results.refreshing ? " finder-section--stale" : ""}`}
          aria-busy={!ready}
        >
          <legend>
            {t("chart.finder.channel.candidates")}
            {found ? (
              <span className="finder-section__meta">
                {tFill("chart.finder.channel.count", { shown: found.candidates.length, total: found.total_candidates })}
              </span>
            ) : null}
          </legend>
          {channels.isError ? <FinderFailure error={channels.error} /> : null}
          {found !== undefined && found.candidates.length === 0 && found.anchor_issue_message === null ? (
            <p className="finder-note">{t("chart.finder.channel.empty")}</p>
          ) : null}
          {(found?.candidates ?? []).map((candidate, index) => {
            const [a, b] = candidate.drawing.anchors;
            return (
              <label key={`${a.bar_date}-${b.bar_date}-${index}`} className="finder-choice finder-choice--candidate">
                <input
                  type="radio"
                  name={`${id}-candidate`}
                  checked={draft.candidate === index}
                  // 앞 입력의 후보를 보이는 동안에는 고르지 않는다 — 고른 자리가 새 결과의 다른 후보에 붙는다
                  disabled={!ready}
                  onChange={() => dispatch({ type: "pickCandidate", index })}
                />
                <span>
                  <strong>{tFill("chart.finder.channel.candidate", { rank: index + 1, from: a.bar_date, to: b.bar_date })}</strong>
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
    </FinderPanelFrame>
  );
};
