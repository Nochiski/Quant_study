import { useId } from "react";

import {
  formatPrice,
  levelText,
  lineKindLabel,
  swingChoiceLabel,
  timeframeLabel,
  type ChartConfluenceBody,
  type ChartInstrumentBody,
  type ChartTarget,
  type SwingChoice,
  type Timeframe,
} from "../../../entities/chart";
import { t, tFill } from "../../../shared/config";
import { changeFromCurrent } from "../model/line-evidence";
import { useAddCandidates } from "../model/use-add-candidates";
import { PREVIEW_REF } from "../model/use-finder-results";
import type { LevelFinder } from "../model/use-level-finder";
import { CreateFooter, FinderFailure, RefreshingNote, WindowFields } from "./finder-fields";
import { FinderPanelFrame } from "./finder-panel-frame";
import { percentText } from "./percent-text";

type CreateRetracementPanelProps = {
  finder: LevelFinder;
  target: ChartTarget;
  timeframe: Timeframe;
  instrument: ChartInstrumentBody;
  /** 현재가(계열 마지막 봉 종가). */
  current: number | null;
  /** 켜고 끌 수 있는 레벨 칸(저장 선 목록 응답 `level_choices`). */
  levelChoices: readonly number[];
  /** 보이는 선의 중첩(미리보기 후보 포함). 이 되돌림이 든 중첩만 보인다. */
  confluence: ChartConfluenceBody | undefined;
};

const SWINGS: readonly SwingChoice[] = ["range_extremes", "latest_major", "latest_confirmed"];

/**
 * 피보나치 되돌림 만들기: 분석 구간 → 기준 스윙(서버가 고른 두 확정 피벗) → 레벨 → 결과 표(레벨마다 이번 주기 가격,
 * 응답 `end.price`) → 중첩(서버 중첩 응답 중 이 미리보기가 든 것) → 차트에 추가.
 */
export const CreateRetracementPanel = ({
  finder,
  target,
  timeframe,
  instrument,
  current,
  levelChoices,
  confluence,
}: CreateRetracementPanelProps) => {
  const id = useId();
  const { panel, dispatch, results } = finder;
  const adding = useAddCandidates(target, timeframe);
  if (panel.view !== "create") return null;
  const { draft } = panel;
  const close = () => dispatch({ type: "close" });
  const found = results.retracement.data;
  const drawing = found?.drawing ?? null;
  const price = (value: number) => formatPrice(value, instrument.kind);
  const levels = draft.levels ?? drawing?.paths.map((path) => path.level ?? 0) ?? [];
  const setLevel = (level: number, on: boolean) => {
    const next = on ? [...levels, level] : levels.filter((item) => item !== level);
    if (next.length > 0) dispatch({ type: "edit", input: { levels: [...next].sort((a, b) => a - b) } });
  };
  const ref = `${PREVIEW_REF}retracement`;
  const overlaps = (confluence?.groups ?? []).filter((group) => group.members.some((member) => member.ref === ref));

  const add = async () => {
    const preview = results.previews.find((item) => item.key === "retracement");
    if (!results.ready || preview === undefined || found === undefined) return;
    if ((await adding.add([preview], found.record))?.failed === 0) close();
  };

  return (
    <FinderPanelFrame
      title={t("chart.finder.retracement.title")}
      subtitle={tFill("chart.finder.previewing", { name: instrument.name, timeframe: timeframeLabel(timeframe) })}
      onClose={close}
      footer={
        <CreateFooter
          addLabel={t("chart.finder.add")}
          canAdd={results.ready && drawing !== null}
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
        <legend>{t("chart.finder.retracement.swing")}</legend>
        <p className="finder-note">{t("chart.finder.retracement.direction")}</p>
        {SWINGS.map((swing) => (
          <label key={swing} className="finder-choice finder-choice--candidate">
            <input
              type="radio"
              name={`${id}-swing`}
              checked={draft.swing === swing}
              onChange={() => dispatch({ type: "edit", input: { swing } })}
            />
            <span>
              <strong>{swingChoiceLabel(swing)}</strong>
              {/* 어떤 두 점을 고르는지는 서버 문장(domain `swing_choice_rule`)이다 — 응답이 오기 전엔 비운다 */}
              <span className="finder-choice__meta">
                {found?.swing_rules.find((item) => item.swing === swing)?.rule ?? ""}
              </span>
            </span>
          </label>
        ))}
        <p className="finder-note">{t("chart.finder.retracement.manual")}</p>
      </fieldset>

      <fieldset className="finder-section">
        <legend>{t("chart.card.levels")}</legend>
        <div className="finder-levels">
          {levelChoices.map((level) => {
            const on = levels.includes(level);
            return (
              <label key={level}>
                <input
                  type="checkbox"
                  checked={on}
                  disabled={on && levels.length === 1}
                  onChange={(event) => setLevel(level, event.target.checked)}
                />
                {levelText(level)}
              </label>
            );
          })}
        </div>
        <label className="finder-choice">
          <input
            type="checkbox"
            checked={draft.extendRight}
            onChange={(event) => dispatch({ type: "edit", input: { extendRight: event.target.checked } })}
          />
          {t("chart.card.extend")}
        </label>
      </fieldset>

      <RefreshingNote refreshing={results.refreshing} />
      <section
        className={`finder-section${results.refreshing ? " finder-section--stale" : ""}`}
        aria-labelledby={`${id}-result`}
        aria-busy={!results.ready}
      >
        <h3 id={`${id}-result`}>{t("chart.finder.result")}</h3>
        {results.retracement.isError ? <FinderFailure error={results.retracement.error} /> : null}
        {found?.message ? <p className="finder-note">{found.message}</p> : null}
        {drawing === null ? null : (
          <>
            <p className="finder-note">
              {tFill("chart.finder.retracement.anchors", {
                from: `${drawing.anchors[0].bar_date} ${price(drawing.anchors[0].price)}`,
                to: `${drawing.anchors[1].bar_date} ${price(drawing.anchors[1].price)}`,
                created: found?.created ?? "—",
              })}
            </p>
            <table className="finder-table">
              <thead>
                <tr>
                  <th scope="col">{t("chart.finder.level")}</th>
                  <th scope="col">{t("chart.finder.price")}</th>
                  <th scope="col">{t("chart.finder.fromCurrent")}</th>
                </tr>
              </thead>
              <tbody>
                {drawing.paths.map((path) => (
                  <tr key={path.level ?? "h"}>
                    <th scope="row">{path.level === null ? "—" : levelText(path.level)}</th>
                    <td>{price(path.end.price)}</td>
                    <td>{percentText(changeFromCurrent(path.end.price, current))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        {overlaps.map((group) => (
          <p key={`${group.low}-${group.high}`} className="finder-note">
            {tFill("chart.finder.retracement.overlap", {
              levels: group.members
                .filter((member) => member.ref === ref && member.level !== null)
                .map((member) => levelText(member.level ?? 0))
                .join(", "),
              low: price(group.low),
              high: price(group.high),
              kinds: group.kinds.map(lineKindLabel).join("·"),
            })}
          </p>
        ))}
        {overlaps.length > 0 && confluence ? <p className="finder-note">{confluence.caution}</p> : null}
      </section>
    </FinderPanelFrame>
  );
};
