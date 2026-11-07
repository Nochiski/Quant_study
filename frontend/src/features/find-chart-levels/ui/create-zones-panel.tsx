import { useId } from "react";

import {
  formatPrice,
  timeframeLabel,
  zoneSideLabel,
  type ChartInstrumentBody,
  type ChartTarget,
  type Timeframe,
} from "../../../entities/chart";
import { t, tFill } from "../../../shared/config";
import { serverParam } from "../model/line-evidence";
import { useAddCandidates } from "../model/use-add-candidates";
import type { LevelFinder } from "../model/use-level-finder";
import { CreateFooter, FinderFailure, ParamField, RefreshingNote, WindowFields } from "./finder-fields";
import { FinderPanelFrame } from "./finder-panel-frame";

type CreateZonesPanelProps = {
  finder: LevelFinder;
  target: ChartTarget;
  timeframe: Timeframe;
  instrument: ChartInstrumentBody;
};

// 묶는 폭 고르기 칸(k). 화면의 고르기 칸일 뿐이고 기본값과 규칙 문장은 응답(`record.params`·`rules`)이 보인다.
const WIDTH_CHOICES: readonly number[] = [0.25, 0.5, 1];

/**
 * 수평 지지·저항 구간 만들기: 분석 구간 → 묶는 폭·파라미터(규칙 문장은 응답 `rules`) → 찾은 구간 표(구간마다 추가 체크·
 * 구분·접촉·생긴 날·지킴/뚫림, 모두 응답 값) → 고른 구간 차트에 추가(구간마다 한 선, 같은 자동 기록). 결과가 지금
 * 입력의 것이 아니면(다시 받는 중) 체크·추가를 막는다.
 */
export const CreateZonesPanel = ({ finder, target, timeframe, instrument }: CreateZonesPanelProps) => {
  const id = useId();
  const { panel, dispatch, results } = finder;
  const adding = useAddCandidates(target, timeframe);
  if (panel.view !== "create") return null;
  const { draft } = panel;
  const close = () => dispatch({ type: "close" });
  const found = results.zones.data;
  const record = found?.record;
  const width = draft.widthAtr ?? serverParam(record?.params, "width_atr");
  const widths =
    width === null || WIDTH_CHOICES.includes(width) ? WIDTH_CHOICES : [...WIDTH_CHOICES, width].sort((a, b) => a - b);
  const price = (value: number) => formatPrice(value, instrument.kind);
  const picked = results.previews;

  const add = async () => {
    if (!results.ready || record === undefined) return;
    if ((await adding.add(picked, record))?.failed === 0) close();
  };

  return (
    <FinderPanelFrame
      title={t("chart.finder.zones.title")}
      subtitle={tFill("chart.finder.previewing", { name: instrument.name, timeframe: timeframeLabel(timeframe) })}
      onClose={close}
      footer={
        <CreateFooter
          addLabel={t("chart.finder.zones.add")}
          canAdd={results.ready && picked.length > 0}
          onAdd={() => void add()}
          onClose={close}
          adding={adding}
        />
      }
    >
      <WindowFields
        window={results.window}
        onChange={(window) => dispatch({ type: "edit", input: { window } })}
        note={t("chart.finder.window.screenNote")}
      />

      <fieldset className="finder-section finder-choices">
        <legend>{t("chart.finder.zones.width")}</legend>
        <div className="finder-chips">
          {widths.map((choice) => (
            <label key={choice} className="finder-choice">
              <input
                type="radio"
                name={`${id}-width`}
                checked={width === choice}
                onChange={() => dispatch({ type: "edit", input: { widthAtr: choice } })}
              />
              {tFill("chart.finder.zones.widthChoice", { k: choice })}
            </label>
          ))}
        </div>
        <details className="finder-details">
          <summary>{t("chart.finder.params.title")}</summary>
          <ParamField
            label={t("chart.finder.params.candidate")}
            value={draft.candidateSensitivity}
            serverValue={serverParam(record?.params, "candidate_sensitivity")}
            step={0.1}
            onCommit={(value) => dispatch({ type: "edit", input: { candidateSensitivity: value } })}
          />
          <ParamField
            label={t("chart.finder.zones.minTouches")}
            value={draft.minTouches}
            serverValue={serverParam(record?.params, "min_touches")}
            step={1}
            onCommit={(value) => dispatch({ type: "edit", input: { minTouches: value } })}
          />
        </details>
        {found ? (
          <ul className="finder-list">
            {found.rules.map((rule) => (
              <li key={rule}>{rule}</li>
            ))}
          </ul>
        ) : null}
      </fieldset>

      <RefreshingNote refreshing={results.refreshing} />
      <section
        className={`finder-section${results.refreshing ? " finder-section--stale" : ""}`}
        aria-labelledby={`${id}-result`}
        aria-busy={!results.ready}
      >
        <h3 id={`${id}-result`}>
          {t("chart.finder.zones.found")}
          {found ? (
            <span className="finder-section__meta">{tFill("chart.finder.zones.count", { count: found.zones.length })}</span>
          ) : null}
        </h3>
        {results.zones.isError ? <FinderFailure error={results.zones.error} /> : null}
        {found !== undefined && found.zones.length === 0 ? (
          <p className="finder-note">{t("chart.finder.zones.empty")}</p>
        ) : null}
        {found !== undefined && found.zones.length > 0 ? (
          <div className="finder-table-scroll">
            <table className="finder-table finder-table--nowrap">
              <thead>
                <tr>
                  <th scope="col">{t("chart.finder.zones.pick")}</th>
                  <th scope="col">{t("chart.finder.zones.range")}</th>
                  <th scope="col">{t("chart.finder.zones.side")}</th>
                  <th scope="col">{t("chart.finder.zones.touches")}</th>
                  <th scope="col">{t("chart.finder.zones.formed")}</th>
                  <th scope="col">{t("chart.finder.zones.heldBroken")}</th>
                </tr>
              </thead>
              <tbody>
                {found.zones.map((zone, index) => {
                  const range = `${price(zone.low)}–${price(zone.high)}`;
                  return (
                    <tr key={`${zone.low}-${zone.high}`}>
                      <td>
                        <input
                          type="checkbox"
                          checked={!draft.excludedZones.includes(index)}
                          // 앞 입력의 결과를 보이는 동안에는 고르지 않는다 — 뺀 자리가 새 결과의 다른 구간에 붙는다
                          disabled={!results.ready}
                          aria-label={tFill("chart.finder.zones.pickOne", { range })}
                          onChange={() => dispatch({ type: "toggleZone", index })}
                        />
                      </td>
                      <th scope="row">{range}</th>
                      <td>{zoneSideLabel(zone.side)}</td>
                      <td>{zone.touches}</td>
                      <td>{zone.formed}</td>
                      <td>{tFill("chart.finder.zones.heldBrokenValue", { held: zone.held, broken: zone.broken })}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
        {found ? <p className="finder-note">{found.basis}</p> : null}
      </section>
    </FinderPanelFrame>
  );
};
