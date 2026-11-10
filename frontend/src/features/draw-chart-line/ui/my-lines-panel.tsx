import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import {
  lineIssueLabel,
  lineTitle,
  lineTone,
  type ChartCandleBody,
  type ChartInstrumentBody,
  type ChartLineBody,
  type LineRemoval,
  type Timeframe,
} from "../../../entities/chart";
import { t, tFill } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import { nudgedAnchors } from "../model/line-edit";
import type { ChartDrawing } from "../model/use-chart-drawing";
import { AddLineForm } from "./add-line-form";
import { SelectedLineCard } from "./selected-line-card";

type MyLinesPanelProps = {
  drawing: ChartDrawing;
  removal: LineRemoval;
  instrument: ChartInstrumentBody;
  /** 그 대상의 저장 선 전부. 목록에는 내 선만 두고, 고른 선 카드는 차트에서 고른 자동 선도 연다. */
  lines: readonly ChartLineBody[];
  timeframe: Timeframe;
  /** 지금 주기의 완성 봉 전부와 보이는 완성 봉. */
  completed: readonly ChartCandleBody[];
  visible: readonly ChartCandleBody[];
  /** 레벨 칸 목록(저장 선 목록 응답 `level_choices`). */
  levelChoices: readonly number[];
};

/**
 * 레이어 칸 "내가 그은 선"(손으로 그었거나 복사한 선 — `lineTone`이 정한다. 자동 선은 자동 분석 후보 칸이 보인다): 선마다 보이기 토글·이름(고르기)·지우기, 빈 상태, 좌표로 추가, 고른 선 카드. 선 이름
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
  levelChoices,
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
  // 목록이 새로 그려진 뒤 초점을 둘 선: 지운 뒤의 이웃 선, 좌표로 더한 새 선. 빈 글자는 "좌표로 추가" 버튼이다.
  const focusAfterChange = useRef<string | null>(null);
  useEffect(() => {
    const target = focusAfterChange.current;
    if (target === null) return;
    const button = target === "" ? null : buttons.current.get(target);
    if (target !== "" && button === undefined) return;
    focusAfterChange.current = null;
    (button ?? addButton.current)?.focus();
  }, [lines]);

  const mine = lines.filter((line) => lineTone(line) === "mine");
  const title = (line: ChartLineBody) => lineTitle(line, instrument.kind, timeframe);
  const remove = (line: ChartLineBody) => {
    // 지우는 요청이 나가 있으면(Delete 연타) 받지 않는다
    if (!removal.removeLine([line], title(line))) return;
    const index = mine.findIndex((item) => item.line_id === line.line_id);
    const neighbour = mine[index + 1] ?? mine[index - 1];
    focusAfterChange.current = neighbour?.line_id ?? "";
    if (drawing.state.selectedId === line.line_id) drawing.dispatch({ type: "select", lineId: null });
  };

  const onNameKey = (event: KeyboardEvent<HTMLButtonElement>, line: ChartLineBody) => {
    if (event.key === "Delete") {
      event.preventDefault();
      remove(line);
      return;
    }
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const direction = event.key === "ArrowUp" ? 1 : -1;
    const large = event.shiftKey;
    // 연달아 누르면 요청이 차례대로 가고, 각 이동은 앞 이동이 저장된 가격에서 센다
    void drawing.edit(line, (latest) => {
      const anchors = nudgedAnchors(latest, instrument, direction, large);
      return anchors === null ? null : { anchors };
    });
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
          onAdded={(line) => {
            setAdding(false);
            focusAfterChange.current = line.line_id;
          }}
        />
      ) : null}
      {mine.length === 0 ? (
        <p className="my-lines__empty">{t("chart.mine.empty")}</p>
      ) : (
        <ul className="my-lines__list">
          {mine.map((line) => {
            const name = title(line);
            return (
              <li key={line.line_id} className="my-lines__row">
                <input
                  type="checkbox"
                  checked={line.visible}
                  aria-label={tFill("chart.mine.show", { title: name })}
                  onChange={(event) => drawing.edit(line, { visible: event.target.checked })}
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
          levelChoices={levelChoices}
        />
      )}
    </section>
  );
};
