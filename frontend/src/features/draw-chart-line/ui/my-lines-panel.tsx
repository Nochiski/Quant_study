import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import {
  lineIssueLabel,
  lineTitle,
  type ChartCandleBody,
  type ChartInstrumentBody,
  type ChartLineBody,
  type Timeframe,
} from "../../../entities/chart";
import { t, tFill } from "../../../shared/config";
import { Badge, Button } from "../../../shared/ui";
import { nudgedAnchors } from "../model/line-edit";
import type { ChartDrawing } from "../model/use-chart-drawing";
import type { useLineRemoval } from "../model/use-line-removal";
import { AddLineForm } from "./add-line-form";
import { SelectedLineCard } from "./selected-line-card";

type MyLinesPanelProps = {
  drawing: ChartDrawing;
  removal: ReturnType<typeof useLineRemoval>;
  instrument: ChartInstrumentBody;
  lines: readonly ChartLineBody[];
  timeframe: Timeframe;
  /** 지금 주기의 완성 봉 전부와 보이는 완성 봉. */
  completed: readonly ChartCandleBody[];
  visible: readonly ChartCandleBody[];
};

/**
 * 레이어 칸 "내가 그은 선": 선마다 보이기 토글·이름(고르기)·지우기, 빈 상태, 좌표로 추가, 고른 선 카드. 선 이름
 * 버튼에 초점이 있으면 ↑/↓ 한 호가, Shift+↑/↓ 1%(수평선), Delete로 지운다.
 */
export const MyLinesPanel = ({
  drawing,
  removal,
  instrument,
  lines,
  timeframe,
  completed,
  visible,
}: MyLinesPanelProps) => {
  const id = useId();
  const [adding, setAdding] = useState(false);
  const scopeKey = `${instrument.kind}|${instrument.code}|${timeframe}`;
  const [scope, setScope] = useState(scopeKey);
  if (scope !== scopeKey) {
    // 렌더 중 상태 맞추기: 좌표 칸의 날짜는 그 주기 봉의 날짜라 계열이 바뀌면 닫는다
    setScope(scopeKey);
    setAdding(false);
  }
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const addButton = useRef<HTMLButtonElement>(null);
  // 지운 뒤 초점을 둘 이웃 선. 목록이 새로 그려진 뒤 옮긴다.
  const focusAfterRemoval = useRef<string | null>(null);
  useEffect(() => {
    const target = focusAfterRemoval.current;
    if (target === null) return;
    const button = target === "" ? null : buttons.current.get(target);
    if (target !== "" && button === undefined) return;
    focusAfterRemoval.current = null;
    (button ?? addButton.current)?.focus();
  }, [lines]);

  const title = (line: ChartLineBody) => lineTitle(line, instrument.kind, timeframe);
  const remove = (line: ChartLineBody) => {
    const index = lines.findIndex((item) => item.line_id === line.line_id);
    const neighbour = lines[index + 1] ?? lines[index - 1];
    focusAfterRemoval.current = neighbour?.line_id ?? "";
    if (drawing.state.selectedId === line.line_id) drawing.dispatch({ type: "select", lineId: null });
    removal.removeLine(line, title(line));
  };

  const onNameKey = (event: KeyboardEvent<HTMLButtonElement>, line: ChartLineBody) => {
    if (event.key === "Delete") {
      event.preventDefault();
      remove(line);
      return;
    }
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    if (drawing.editing(line.line_id)) return;
    const anchors = nudgedAnchors(line, instrument, event.key === "ArrowUp" ? 1 : -1, event.shiftKey);
    if (anchors !== null) drawing.edit(line, { anchors });
  };

  const selected = lines.find((line) => line.line_id === drawing.state.selectedId) ?? null;

  return (
    <section className="my-lines" aria-labelledby={`${id}-title`}>
      <div className="my-lines__head">
        <h2 id={`${id}-title`}>{t("chart.mine.title")}</h2>
        <Button
          ref={addButton}
          size="small"
          aria-expanded={adding}
          onClick={() => setAdding((open) => !open)}
        >
          {t("chart.mine.add")}
        </Button>
      </div>
      {adding ? (
        <AddLineForm
          drawing={drawing}
          completed={completed}
          visible={visible}
          instrumentKind={instrument.kind}
          onDone={() => setAdding(false)}
        />
      ) : null}
      {lines.length === 0 ? (
        <p className="my-lines__empty">{t("chart.mine.empty")}</p>
      ) : (
        <ul className="my-lines__list">
          {lines.map((line) => {
            const name = title(line);
            return (
              <li key={line.line_id} className="my-lines__row">
                <input
                  type="checkbox"
                  checked={line.visible}
                  aria-label={tFill("chart.mine.show", { title: name })}
                  onChange={() => drawing.edit(line, { visible: !line.visible })}
                />
                <button
                  type="button"
                  ref={(element) => {
                    if (element === null) buttons.current.delete(line.line_id);
                    else buttons.current.set(line.line_id, element);
                  }}
                  className="my-lines__name"
                  aria-pressed={line.line_id === drawing.state.selectedId}
                  onClick={() =>
                    drawing.dispatch({
                      type: "select",
                      lineId: line.line_id === drawing.state.selectedId ? null : line.line_id,
                    })
                  }
                  onKeyDown={(event) => onNameKey(event, line)}
                >
                  {name}
                  {line.source === "auto" ? (
                    <>
                      {" "}
                      <Badge tone="neutral">{t("chart.mine.auto")}</Badge>
                    </>
                  ) : null}
                  {line.issue === null ? null : (
                    <span className="my-lines__issue">{lineIssueLabel(line.issue)}</span>
                  )}
                </button>
                <button
                  type="button"
                  className="my-lines__remove"
                  aria-label={tFill("chart.mine.remove", { title: name })}
                  disabled={removal.removing}
                  onClick={() => remove(line)}
                >
                  <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor"
                    strokeWidth="1.6" strokeLinecap="round" aria-hidden="true" focusable="false">
                    <path d="M5 5l10 10M15 5L5 15" />
                  </svg>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <p className="my-lines__keys">{t("chart.mine.keys")}</p>
      {selected === null ? null : (
        <SelectedLineCard
          line={selected}
          drawing={drawing}
          completed={completed}
          instrumentKind={instrument.kind}
          timeframe={timeframe}
        />
      )}
    </section>
  );
};
