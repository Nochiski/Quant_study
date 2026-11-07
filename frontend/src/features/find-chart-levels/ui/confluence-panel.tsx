import { useId } from "react";

import {
  formatPrice,
  levelText,
  lineKindLabel,
  lineTitle,
  lineTone,
  timeframeLabel,
  type ChartConfluenceBody,
  type ChartInstrumentBody,
  type ChartLineBody,
  type Timeframe,
} from "../../../entities/chart";
import { t, tFill } from "../../../shared/config";
import { changeFromCurrent } from "../model/line-evidence";
import { PREVIEW_REF } from "../model/use-finder-results";
import type { LevelFinder } from "../model/use-level-finder";
import { FinderFailure } from "./finder-fields";
import { FinderPanelFrame } from "./finder-panel-frame";
import { percentText } from "./percent-text";

type ConfluencePanelProps = {
  finder: LevelFinder;
  confluence: ChartConfluenceBody | undefined;
  error: unknown;
  /** 내 선(손으로 그었거나 복사한 선)도 중첩 계산에 넣는가(요청 `include_mine`). */
  includeMine: boolean;
  onIncludeMine: (include: boolean) => void;
  /** 그 대상의 저장 선(중첩 구성 선 ref → 이름). */
  lines: readonly ChartLineBody[];
  instrument: ChartInstrumentBody;
  timeframe: Timeframe;
  current: number | null;
};


/**
 * 중첩 근거: 서로 다른 종류 2개 이상이 덮는 가격대(서버 `/confluence` 응답 그대로)마다 교집합·현재가 대비·종류·겹친 선,
 * 허용 폭, 서버 주의 문장. "내가 그은 선도 넣기"는 요청 칸(`include_mine`)이라 선의 보이기 저장 칸을 바꾸지 않는다.
 */
export const ConfluencePanel = ({
  finder,
  confluence,
  error,
  includeMine,
  onIncludeMine,
  lines,
  instrument,
  timeframe,
  current,
}: ConfluencePanelProps) => {
  const id = useId();
  const close = () => finder.dispatch({ type: "close" });
  const price = (value: number) => formatPrice(value, instrument.kind);
  const memberName = (ref: string) => {
    if (ref.startsWith(PREVIEW_REF))
      return t("chart.finder.confluence.preview");
    const line = lines.find((item) => item.line_id === ref);
    if (line === undefined) return ref;
    const owner =
      lineTone(line) === "mine"
        ? t("chart.finder.confluence.mine")
        : t("chart.finder.confluence.auto");
    return `${lineTitle(line, instrument.kind, timeframe)} · ${owner}`;
  };
  const groups = confluence?.groups ?? [];
  return (
    <FinderPanelFrame
      title={t("chart.finder.confluence.title")}
      subtitle={tFill("chart.finder.evidence.subtitle", {
        name: instrument.name,
        timeframe: timeframeLabel(timeframe),
      })}
      onClose={close}
    >
      <label className="finder-choice">
        <input
          type="checkbox"
          checked={includeMine}
          onChange={(event) => onIncludeMine(event.target.checked)}
        />
        {t("chart.finder.confluence.includeMine")}
      </label>
      {error ? <FinderFailure error={error} /> : null}
      {confluence ? (
        <ul className="finder-list">
          {confluence.rules.map((rule) => (
            <li key={rule}>{rule}</li>
          ))}
        </ul>
      ) : null}
      {confluence !== undefined && groups.length === 0 ? (
        <p className="finder-note">{t("chart.finder.confluence.empty")}</p>
      ) : null}
      {groups.map((group, index) => {
        const middle = (group.low + group.high) / 2;
        return (
          <section
            key={`${group.low}-${group.high}`}
            className="finder-section finder-group"
            aria-labelledby={`${id}-${index}`}
          >
            <h3 id={`${id}-${index}`}>
              {tFill("chart.finder.confluence.group", {
                n: index + 1,
                low: price(group.low),
                high: price(group.high),
              })}
            </h3>
            <p className="finder-note">
              {tFill("chart.finder.confluence.groupMeta", {
                gap: percentText(changeFromCurrent(middle, current)),
                kinds: group.kinds.map(lineKindLabel).join("·"),
                count: group.kinds.length,
              })}
            </p>
            <ul className="finder-list">
              {group.members.map((member) => (
                <li key={`${member.ref}-${member.level ?? "h"}`}>
                  {memberName(member.ref)}
                  {member.level === null
                    ? ""
                    : ` · ${tFill("chart.finder.confluence.level", { level: levelText(member.level) })}`}
                  {` · ${price(member.low)}${member.high === member.low ? "" : `–${price(member.high)}`}`}
                </li>
              ))}
            </ul>
          </section>
        );
      })}
      {confluence && confluence.skipped_line_ids.length > 0 ? (
        <p className="finder-note">
          {tFill("chart.finder.confluence.skipped", {
            count: confluence.skipped_line_ids.length,
          })}
        </p>
      ) : null}
      {confluence ? (
        <section className="finder-section" aria-labelledby={`${id}-caution`}>
          <h3 id={`${id}-caution`}>{t("chart.finder.evidence.caution")}</h3>
          <ul className="finder-list">
            <li>{confluence.caution}</li>
            {includeMine ? <li>{t("chart.finder.confluence.bias")}</li> : null}
            <li>{t("chart.finder.evidence.caution.signal")}</li>
          </ul>
        </section>
      ) : null}
    </FinderPanelFrame>
  );
};
