import { useId, useState, type FormEvent } from "react";

import type { ChartCandleBody, InstrumentKind, LineKind } from "../../../entities/chart";
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
  onDone: () => void;
};

/**
 * 좌표로 추가: 도구를 고르고 기준점마다 봉 날짜와 가격을 넣는다(마우스 없이 선을 긋는 길). 수평선은 가격만 받고 첫
 * 봉부터 그린다.
 */
export const AddLineForm = ({ drawing, completed, visible, instrumentKind, onDone }: AddLineFormProps) => {
  const id = useId();
  const [kind, setKind] = useState<LineKind>("hline");
  const [drafts, setDrafts] = useState<AnchorDraft[]>(() => initialDrafts("hline", visible, instrumentKind));
  const anchors = anchorsOf(drafts);
  const first = completed[0];

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (anchors === null || first === undefined) return;
    drawing.save(
      kind,
      kind === "hline" ? [{ bar_date: first.start, price: anchors[0].price }] : anchors,
      onDone,
    );
  };

  return (
    <form className="add-line-form" aria-label={t("chart.mine.add")} onSubmit={submit}>
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
          candles={kind === "hline" ? null : completed}
          instrumentKind={instrumentKind}
          onChange={(next) =>
            setDrafts((current) => current.map((item, at) => (at === index ? next : item)))
          }
        />
      ))}
      <div className="add-line-form__actions">
        <p className="add-line-form__note">
          {t(kind === "hline" ? "chart.add.hlineNote" : "chart.add.note")}
        </p>
        <Button tone="primary" type="submit" disabled={anchors === null || drawing.saving}>
          {t("chart.add.submit")}
        </Button>
      </div>
    </form>
  );
};
