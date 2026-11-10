import { useId, useState, type MouseEvent } from "react";

import {
  formatPrice,
  lineIssueLabel,
  regimeVerdictLabel,
  type ChartInstrumentBody,
  type ChartLineBody,
  type DateRange,
  type Timeframe,
} from "../../../entities/chart";
import { t, tFill } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import { autoRows, type AutoRow } from "../model/auto-rows";
import type { FinderTool } from "../model/finder-panel";
import { autoLineTitle, zoneBounds } from "../model/line-evidence";
import type { LevelFinder } from "../model/use-level-finder";
import { useLineVisibility } from "../model/use-line-visibility";

type AutoLinesPanelProps = {
  finder: LevelFinder;
  /** 그 대상의 저장 선 전부. 자동 선만 줄로 보인다. */
  lines: readonly ChartLineBody[];
  instrument: ChartInstrumentBody;
  timeframe: Timeframe;
  /** 수동 그리기 중이면 자동 후보를 가려 두고 안내만 보인다. */
  manual: boolean;
  /** 보이는 선의 중첩 수(서버 응답, 아직 없으면 null)와 내 선 포함 여부. */
  confluenceCount: number | null;
  includeMine: boolean;
  /** 지우기(묶음은 함께, 되돌리기 한 번에 함께 되살아난다). */
  onRemove: (lines: readonly ChartLineBody[], title: string) => void;
  removing: boolean;
  /** 지금 화면에 보이는 완성 봉 구간 — 되돌림·수평 구간 생성 패널의 기본 분석 구간("기본값은 화면 기간"). */
  screenWindow: DateRange | null;
};

const NEW_TOOLS: readonly FinderTool[] = [
  "hline",
  "fib_retracement",
  "fib_channel",
];

/** 연 버튼 이름 — 패널을 닫으면 레이어 칸이 다시 그려진 뒤 이 이름의 버튼으로 초점을 돌려준다(`data-finder-key`). */
const finderKey = {
  create: (tool: FinderTool) => `new:${tool}`,
  evidence: (row: AutoRow) => `evidence:${row.key}`,
  confluence: "confluence",
} as const;

const opener = (event: MouseEvent<HTMLButtonElement>) =>
  event.currentTarget.dataset.finderKey ?? "";

/**
 * 레이어 칸 "자동 분석 후보": 저장한 자동 선을 만든 것마다 한 줄(수평 구간은 펼쳐서 구간마다 켜고 끈다 — 한 구간만 보기),
 * 줄마다 보이기·근거·지우기, 중첩 한 줄(근거 → 중첩 패널), "새로 만들기: 수평 구간·되돌림·채널".
 */
export const AutoLinesPanel = ({
  finder,
  lines,
  instrument,
  timeframe,
  manual,
  confluenceCount,
  includeMine,
  onRemove,
  removing,
  screenWindow,
}: AutoLinesPanelProps) => {
  const id = useId();
  const [open, setOpen] = useState<readonly string[]>([]);
  const visibility = useLineVisibility();
  const onToggle = visibility.setVisible;
  const rows = autoRows(lines);
  const price = (value: number) => formatPrice(value, instrument.kind);
  const { dispatch } = finder;

  const rowTitle = (row: AutoRow) => autoLineTitle(row.lines, timeframe);
  const rowDetail = (row: AutoRow) => {
    const [first] = row.lines;
    const auto = first.auto;
    if (row.kind === "hline")
      return tFill("chart.finder.zones.rowMeta", {
        k: String(auto?.params?.width_atr ?? "—"),
        touches: String(auto?.params?.min_touches ?? "—"),
      });
    if (row.kind === "fib_retracement")
      return tFill("chart.finder.retracement.rowMeta", {
        from: price(first.anchors[0].price),
        to: price(first.anchors[1].price),
        created: first.created ?? "—",
      });
    return tFill("chart.finder.channel.rowMeta", {
      start: auto?.regime_window_start ?? "—",
      end: auto?.regime_window_end ?? "—",
      verdict: auto?.regime ? regimeVerdictLabel(auto.regime.verdict) : "—",
      created: first.created ?? "—",
    });
  };

  return (
    <section className="auto-lines" aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`} tabIndex={-1}>
        {t("chart.finder.list.title")}
      </h2>
      {manual ? (
        <p className="finder-note">{t("chart.finder.list.hidden")}</p>
      ) : (
        <>
          <p className="finder-note">{t("chart.finder.list.basis")}</p>
          {visibility.failed > 0 ? (
            <p className="finder-warning" role="status">
              {tFill("chart.finder.list.toggleFailed", { count: String(visibility.failed) })}{" "}
              <Button size="small" tone="ghost" onClick={visibility.dismiss}>
                {t("chart.undo.dismiss")}
              </Button>
            </p>
          ) : null}
          {rows.length === 0 ? (
            <p className="finder-note">{t("chart.finder.list.empty")}</p>
          ) : null}
          <ul className="auto-lines__list">
            {rows.map((row) => {
              const title = rowTitle(row);
              const shown = row.lines.every((line) => line.visible);
              const some = row.lines.some((line) => line.visible);
              const expanded = open.includes(row.key);
              const issue =
                row.lines.find((line) => line.issue !== null)?.issue ?? null;
              return (
                <li key={row.key} className="auto-lines__row">
                  <div className="auto-lines__line">
                    <input
                      type="checkbox"
                      checked={shown}
                      ref={(element) => {
                        if (element !== null)
                          element.indeterminate = some && !shown;
                      }}
                      aria-label={tFill("chart.mine.show", { title })}
                      onChange={(event) =>
                        onToggle(row.lines, event.target.checked)
                      }
                    />
                    <span className="auto-lines__text">
                      <span
                        className={`auto-lines__name auto-lines__name--${row.kind}`}
                      >
                        {title}
                      </span>
                      <span className="auto-lines__meta">{rowDetail(row)}</span>
                      {issue === null ? null : (
                        <span className="auto-lines__issue">
                          {lineIssueLabel(issue)}
                        </span>
                      )}
                    </span>
                    {row.kind === "hline" && row.lines.length > 0 ? (
                      <button
                        type="button"
                        className="auto-lines__link"
                        aria-expanded={expanded}
                        aria-label={tFill("chart.finder.list.expand", {
                          title,
                        })}
                        onClick={() =>
                          setOpen((keys) =>
                            expanded
                              ? keys.filter((key) => key !== row.key)
                              : [...keys, row.key],
                          )
                        }
                      >
                        {t("chart.finder.list.each")}
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="auto-lines__link"
                      data-finder-key={finderKey.evidence(row)}
                      aria-label={tFill("chart.finder.list.evidence", {
                        title,
                      })}
                      onClick={(event) =>
                        dispatch({
                          type: "openEvidence",
                          lineIds: row.lines.map((line) => line.line_id),
                          returnKey: opener(event),
                        })
                      }
                    >
                      {t("chart.finder.list.evidenceShort")}
                    </button>
                    <button
                      type="button"
                      className="auto-lines__remove"
                      aria-label={tFill("chart.mine.remove", { title })}
                      disabled={removing}
                      onClick={() => onRemove(row.lines, title)}
                    >
                      <svg
                        width="16"
                        height="16"
                        viewBox="0 0 20 20"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinecap="round"
                        aria-hidden="true"
                        focusable="false"
                      >
                        <path d="M5 5l10 10M15 5L5 15" />
                      </svg>
                    </button>
                  </div>
                  {expanded ? (
                    <ul className="auto-lines__zones">
                      {row.lines.map((line) => {
                        const { low, high } = zoneBounds(line);
                        const range = `${price(low)}–${price(high)}`;
                        return (
                          <li key={line.line_id}>
                            <label>
                              <input
                                type="checkbox"
                                checked={line.visible}
                                onChange={(event) =>
                                  onToggle([line], event.target.checked)
                                }
                              />
                              {range}
                              {line.created === null
                                ? ""
                                : ` · ${tFill("chart.finder.list.formed", { date: line.created })}`}
                            </label>
                            <button
                              type="button"
                              className="auto-lines__link"
                              aria-label={tFill("chart.finder.list.only", {
                                range,
                              })}
                              onClick={() => {
                                onToggle([line], true);
                                const others = row.lines.filter(
                                  (item) =>
                                    item.line_id !== line.line_id &&
                                    item.visible,
                                );
                                if (others.length > 0) onToggle(others, false);
                              }}
                            >
                              {t("chart.finder.list.onlyShort")}
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  ) : null}
                </li>
              );
            })}
            <li className="auto-lines__row">
              <div className="auto-lines__line">
                <span className="auto-lines__text">
                  <span className="auto-lines__name auto-lines__name--confluence">
                    {confluenceCount === null
                      ? t("chart.finder.list.confluence")
                      : tFill("chart.finder.list.confluenceCount", {
                          count: confluenceCount,
                        })}
                  </span>
                  <span className="auto-lines__meta">
                    {includeMine
                      ? t("chart.finder.list.confluenceMine")
                      : t("chart.finder.list.confluenceAuto")}
                  </span>
                </span>
                <button
                  type="button"
                  className="auto-lines__link"
                  data-finder-key={finderKey.confluence}
                  aria-label={t("chart.finder.list.confluenceEvidence")}
                  onClick={(event) =>
                    dispatch({
                      type: "openConfluence",
                      returnKey: opener(event),
                    })
                  }
                >
                  {t("chart.finder.list.evidenceShort")}
                </button>
              </div>
            </li>
          </ul>
          <div
            className="auto-lines__new"
            role="group"
            aria-label={t("chart.finder.list.new")}
          >
            <span>{t("chart.finder.list.new")}</span>
            {NEW_TOOLS.map((tool) => (
              <Button
                key={tool}
                size="small"
                data-finder-key={finderKey.create(tool)}
                onClick={(event) =>
                  dispatch({
                    type: "openCreate",
                    tool,
                    // 채널은 서버 기본 판정 구간(가운데 제안 칩), 되돌림·수평 구간은 화면 기간에서 시작한다
                    seed:
                      tool === "fib_channel" ? {} : { window: screenWindow },
                    returnKey: opener(event),
                  })
                }
              >
                {t(`chart.finder.new.${tool}`)}
              </Button>
            ))}
          </div>
        </>
      )}
    </section>
  );
};
