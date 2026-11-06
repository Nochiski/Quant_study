import { useId, useState, type FormEvent } from "react";

import type { ChartCandleBody, ChartLineBody, InstrumentKind, LineKind } from "../../../entities/chart";
import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import { anchorName, anchorsOf, initialDrafts, type AnchorDraft } from "../model/anchor-draft";
import type { ChartDrawing } from "../model/use-chart-drawing";
import { AnchorRow } from "./anchor-row";

const KINDS: readonly LineKind[] = ["hline", "fib_retracement", "fib_channel"];

type AddLineFormProps = {
  drawing: ChartDrawing;
  /** 지금 주기의 완성 봉 전부(날짜 목록). */
  completed: readonly ChartCandleBody[];
  /** 지금 보이는 완성 봉(처음 날짜를 고르게 나누는 범위). */
  visible: readonly ChartCandleBody[];
  instrumentKind: InstrumentKind;
  /** 저장했다 — 칸을 닫고 새 선으로 초점을 옮긴다. */
  onAdded: (line: ChartLineBody) => void;
};

/**
 * 좌표로 추가: 도구를 고르고 기준점마다 봉 날짜와 가격을 넣는다(마우스 없이 선을 긋는 길). 기준점은 차트를 눌러
 * 그릴 때와 같이 "고른 봉의 날짜와 가격"이다.
 */
export const AddLineForm = ({ drawing, completed, visible, instrumentKind, onAdded }: AddLineFormProps) => {
  const id = useId();
  const [kind, setKind] = useState<LineKind>("hline");
  const [drafts, setDrafts] = useState<AnchorDraft[]>(() => initialDrafts("hline", visible, instrumentKind));
  const anchors = anchorsOf(drafts);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (anchors === null) return;
    const line = await drawing.save(kind, anchors);
    if (line !== null) onAdded(line);
  };

  return (
    <form className="add-line-form" aria-label={t("chart.mine.add")} onSubmit={(event) => void submit(event)}>
      <div className="add-line-form__kind">
        <label htmlFor={`${id}-kind`}>{t("chart.add.kind")}</label>
        <select
          id={`${id}-kind`}
          className="ui-select"
          value={kind}
          onChange={(event) => {
            const next = event.target.value as LineKind;
            setKind(next);
            setDrafts(initialDrafts(next, visible, instrumentKind));
          }}
        >
          {KINDS.map((item) => (
            <option key={item} value={item}>
              {t(`chart.add.kind.${item}`)}
            </option>
          ))}
        </select>
      </div>
      {drafts.map((draft, index) => (
        <AnchorRow
          key={`${kind}-${index}`}
          name={anchorName(kind, index)}
          value={draft}
          candles={completed}
          instrumentKind={instrumentKind}
          onChange={(next) =>
            setDrafts((current) => current.map((item, at) => (at === index ? next : item)))
          }
        />
      ))}
      <div className="add-line-form__actions">
        <p className="add-line-form__note">{t("chart.add.note")}</p>
        <Button tone="primary" type="submit" disabled={anchors === null || drawing.saving}>
          {t("chart.add.submit")}
        </Button>
      </div>
    </form>
  );
};
