import { useId, useState } from "react";

import {
  lineKindLabel,
  timeframeLabel,
  type ChartCandleBody,
  type ChartLineBody,
  type InstrumentKind,
  type Timeframe,
} from "../../../entities/chart";
import { t, tFill } from "../../../shared/config";
import { anchorName, anchorsOf, draftsOf, type AnchorDraft } from "../model/anchor-draft";
import { isDraggable } from "../model/line-edit";
import type { ChartDrawing } from "../model/use-chart-drawing";
import { AnchorRow } from "./anchor-row";

const drawnLevels = (line: ChartLineBody): number[] =>
  line.paths.flatMap((path) => (path.level === null ? [] : [path.level]));

type SelectedLineCardProps = {
  line: ChartLineBody;
  drawing: ChartDrawing;
  completed: readonly ChartCandleBody[];
  instrumentKind: InstrumentKind;
  timeframe: Timeframe;
  /**
   * 레벨 칸 목록(저장 선 목록 응답 `level_choices`, owner는 backend domain). 켜진 칸은 선에 그려진 레벨(응답
   * `paths`의 `level`)이다 — 레벨을 비운 선은 서버가 종류별 기본으로 그린다.
   */
  levelChoices: readonly number[];
};

/**
 * 고른 선 카드: 기준점(날짜·가격), 메모, 잠금, 레벨, 오른쪽 연장. 고칠 때마다 선 칸 전체를 실어 저장한다. 잠긴 선·
 * 자동 선(기준점 고정)·다른 주기로 만든 선은 기준점 칸을 잠그고 이유를 적는다.
 */
export const SelectedLineCard = ({
  line,
  drawing,
  completed,
  instrumentKind,
  timeframe,
  levelChoices,
}: SelectedLineCardProps) => {
  const id = useId();
  const version = `${line.line_id}|${line.updated_at}`;
  const [shown, setShown] = useState(version);
  const [drafts, setDrafts] = useState<AnchorDraft[]>(() =>
    draftsOf(line.anchors, completed, instrumentKind),
  );
  const [memo, setMemo] = useState(line.memo);
  if (shown !== version) {
    // 렌더 중 상태 맞추기: 다른 선을 고르거나 저장 응답이 오면 칸을 그 선 값으로 다시 채운다
    setShown(version);
    setDrafts(draftsOf(line.anchors, completed, instrumentKind));
    setMemo(line.memo);
  }
  const anchorsEditable = isDraggable(line, timeframe);
  const levels = drawnLevels(line);
  const choices = [...new Set([...levelChoices, ...levels])].sort((a, b) => a - b);

  const commitAnchors = (next: AnchorDraft[]) => {
    const anchors = anchorsOf(next);
    if (anchors === null) return;
    // 보낼 때의 최신 기준점과 보이는 자릿수로 견준다 — 응답 가격의 마지막 자리 꼬리만 다른 것은 고친 것이
    // 아니고, 나가기·Enter가 둘 다 와도 한 번만 보낸다
    void drawing.edit(line, (latest) => {
      const saved = draftsOf(latest.anchors, completed, instrumentKind);
      const changed = next.some(
        (draft, index) => draft.barDate !== saved[index]?.barDate || draft.price !== saved[index]?.price,
      );
      return changed ? { anchors } : null;
    });
  };

  // 메모는 나가기·Enter에서 저장한다. 둘 다 와도(Enter 뒤 나가기) 보낼 때의 최신 메모와 같으면 보내지 않는다.
  const saveMemo = () =>
    void drawing.edit(line, (latest) => (latest.memo === memo ? null : { memo }));

  // 레벨 하나 켜고 끄기: 보낼 때의 최신 선에 그려진 레벨에서 그 레벨만 바꾼다(연달아 눌러도 앞 변경이 남는다).
  const setLevel = (level: number, on: boolean) =>
    void drawing.edit(line, (latest) => {
      const current = drawnLevels(latest).filter((value) => value !== level);
      const next = on ? [...current, level].sort((a, b) => a - b) : current;
      return next.length === 0 ? null : { levels: next };
    });

  const note = line.source === "auto"
    ? t("chart.card.autoFixed")
    : line.timeframe !== timeframe
      ? tFill("chart.card.otherTimeframe", { timeframe: timeframeLabel(line.timeframe) })
      : line.locked
        ? t("chart.card.lockedNote")
        : null;

  return (
    <section className="selected-line-card" aria-labelledby={`${id}-title`}>
      <div className="selected-line-card__head">
        <h3 id={`${id}-title`}>{tFill("chart.card.title", { kind: lineKindLabel(line.kind) })}</h3>
        <button
          type="button"
          className="selected-line-card__close"
          aria-label={t("chart.card.close")}
          onClick={() => drawing.dispatch({ type: "select", lineId: null })}
        >
          <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor"
            strokeWidth="1.6" strokeLinecap="round" aria-hidden="true" focusable="false">
            <path d="M5 5l10 10M15 5L5 15" />
          </svg>
        </button>
      </div>
      {drafts.map((draft, index) => (
        <AnchorRow
          key={index}
          name={anchorName(line.kind, index)}
          value={draft}
          candles={completed}
          instrumentKind={instrumentKind}
          disabled={!anchorsEditable}
          onChange={(next, commit) => {
            const all = drafts.map((item, at) => (at === index ? next : item));
            setDrafts(all);
            if (commit) commitAnchors(all);
          }}
        />
      ))}
      {note === null ? null : <p className="selected-line-card__note">{note}</p>}
      <div className="selected-line-card__memo">
        <label htmlFor={`${id}-memo`}>{t("chart.card.memo")}</label>
        <input
          id={`${id}-memo`}
          className="ui-input"
          type="text"
          value={memo}
          onChange={(event) => setMemo(event.target.value)}
          onBlur={saveMemo}
          onKeyDown={(event) => {
            if (event.key === "Enter") saveMemo();
          }}
        />
      </div>
      {line.kind === "hline" ? null : (
        <fieldset className="selected-line-card__levels">
          <legend>{t("chart.card.levels")}</legend>
          <div className="selected-line-card__level-list">
            {choices.map((level) => {
              const on = levels.includes(level);
              return (
                <label key={level}>
                  <input
                    type="checkbox"
                    checked={on}
                    // 마지막 하나는 끌 수 없다 — 빈 레벨 목록은 그릴 선이 없다(서버도 거절한다)
                    disabled={(on && levels.length === 1) || line.issue !== null}
                    onChange={(event) => setLevel(level, event.target.checked)}
                  />
                  {String(level)}
                </label>
              );
            })}
          </div>
          {levels.length === 1 ? (
            <p className="selected-line-card__note">{t("chart.card.lastLevel")}</p>
          ) : null}
        </fieldset>
      )}
      <label className="selected-line-card__check">
        <input
          type="checkbox"
          checked={line.extend_right}
          onChange={(event) => drawing.edit(line, { extend_right: event.target.checked })}
        />
        {t("chart.card.extend")}
      </label>
      <label className="selected-line-card__check">
        <input
          type="checkbox"
          checked={line.locked}
          onChange={(event) => drawing.edit(line, { locked: event.target.checked })}
        />
        {t("chart.card.locked")}
      </label>
    </section>
  );
};
