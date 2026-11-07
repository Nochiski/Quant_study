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
import { Button } from "../../../shared/ui";
import { useAddCandidates } from "../model/use-add-candidates";
import type { LevelFinder } from "../model/use-level-finder";
import { serverParam } from "../model/line-evidence";
import { FinderFailure, ParamField, WindowFields } from "./finder-fields";
import { FinderPanelFrame } from "./finder-panel-frame";

type CreateZonesPanelProps = {
  finder: LevelFinder;
  target: ChartTarget;
  timeframe: Timeframe;
  instrument: ChartInstrumentBody;
};

// 묶는 폭 고르기 칸(× ATR). 화면의 고르기 칸일 뿐이고 기본값은 응답 기록(`record.params.width_atr`)이 보인다.
const WIDTH_CHOICES: readonly number[] = [0.25, 0.5, 1];

/**
 * 수평 지지·저항 구간 만들기: 분석 구간 → 묶는 폭·파라미터 → 찾은 구간 표(구간마다 추가 체크·구분·접촉·생긴 날·
 * 지킴/뚫림, 모두 응답 값) → 고른 구간 차트에 추가(구간마다 한 선, 같은 자동 기록).
 */
export const CreateZonesPanel = ({
  finder,
  target,
  timeframe,
  instrument,
}: CreateZonesPanelProps) => {
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
    width === null || WIDTH_CHOICES.includes(width)
      ? WIDTH_CHOICES
      : [...WIDTH_CHOICES, width].sort();
  const price = (value: number) => formatPrice(value, instrument.kind);
  const picked = results.previews;

  // 저장 중에도 단추를 끄지 않는다(꺼지면 초점을 잃는다) — 겹친 누르기만 받지 않는다
  const add = async () => {
    if (adding.pending) return;
    if (
      record !== undefined &&
      picked.length > 0 &&
      (await adding.add(picked, record))
    )
      close();
  };

  return (
    <FinderPanelFrame
      title={t("chart.finder.zones.title")}
      subtitle={tFill("chart.finder.previewing", {
        name: instrument.name,
        timeframe: timeframeLabel(timeframe),
      })}
      onClose={close}
      footer={
        <>
          <Button
            tone="primary"
            disabled={picked.length === 0}
            onClick={() => void add()}
          >
            {t("chart.finder.zones.add")}
          </Button>
          <Button onClick={close}>{t("chart.finder.cancel")}</Button>
        </>
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
                onChange={() =>
                  dispatch({ type: "edit", input: { widthAtr: choice } })
                }
              />
              {tFill("chart.finder.zones.widthChoice", { k: choice })}
            </label>
          ))}
        </div>
        <p className="finder-note">{t("chart.finder.zones.widthNote")}</p>
        <details className="finder-details">
          <summary>{t("chart.finder.params.title")}</summary>
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
            label={t("chart.finder.zones.minTouches")}
            value={draft.minTouches}
            serverValue={serverParam(record?.params, "min_touches")}
            step={1}
            onCommit={(value) =>
              dispatch({ type: "edit", input: { minTouches: value } })
            }
          />
          <p className="finder-note">{t("chart.finder.zones.touchNote")}</p>
        </details>
      </fieldset>

      <section className="finder-section" aria-labelledby={`${id}-result`}>
        <h3 id={`${id}-result`}>
          {t("chart.finder.zones.found")}
          {found ? (
            <span className="finder-section__meta">
              {tFill("chart.finder.zones.count", { count: found.zones.length })}
            </span>
          ) : null}
        </h3>
        {results.zones.isError ? (
          <FinderFailure error={results.zones.error} />
        ) : null}
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
                          aria-label={tFill("chart.finder.zones.pickOne", {
                            range,
                          })}
                          onChange={() =>
                            dispatch({ type: "toggleZone", index })
                          }
                        />
                      </td>
                      <th scope="row">{range}</th>
                      <td>{zoneSideLabel(zone.side)}</td>
                      <td>{zone.touches}</td>
                      <td>{zone.formed}</td>
                      <td>
                        {tFill("chart.finder.zones.heldBrokenValue", {
                          held: zone.held,
                          broken: zone.broken,
                        })}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
        {found ? <p className="finder-note">{found.basis}</p> : null}
      </section>
      {adding.error ? <FinderFailure error={adding.error} /> : null}
    </FinderPanelFrame>
  );
};
