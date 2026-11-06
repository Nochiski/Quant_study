import { useId } from "react";

import {
  CANDLE_FIELDS,
  type CandleField,
  type ChartCandleBody,
  type InstrumentKind,
} from "../../../entities/chart";
import { t } from "../../../shared/config";
import { withBar, type AnchorDraft } from "../model/anchor-draft";

type AnchorRowProps = {
  /** 줄 이름(예: "먼저 점 (1)"). 칸의 접근 이름 앞에 붙는다. */
  name: string;
  value: AnchorDraft;
  /** 고를 수 있는 봉(완성 봉). 수평선처럼 날짜 없이 가격만 받는 줄은 비운다. */
  candles: readonly ChartCandleBody[] | null;
  instrumentKind: InstrumentKind;
  disabled?: boolean;
  /** `commit`은 고르기가 끝났다는 뜻이다 — 날짜·붙일 값 고르기와 가격 칸 나가기·Enter. */
  onChange: (next: AnchorDraft, commit: boolean) => void;
};

/** 기준점 한 줄: 봉(날짜) select, 붙일 값 select, 가격 입력. 모두 native 요소라 키보드로 다 된다. */
export const AnchorRow = ({
  name,
  value,
  candles,
  instrumentKind,
  disabled = false,
  onChange,
}: AnchorRowProps) => {
  const id = useId();
  const pick = (change: Partial<Pick<AnchorDraft, "barDate" | "field">>) =>
    onChange(candles === null ? { ...value, ...change } : withBar(value, candles, instrumentKind, change), true);
  return (
    <div className="anchor-row" role="group" aria-label={name}>
      <span className="anchor-row__name" aria-hidden="true">
        {name}
      </span>
      {candles === null ? null : (
        <>
          <div className="anchor-row__field">
            <label htmlFor={`${id}-date`}>{t("chart.anchor.date")}</label>
            <select
              id={`${id}-date`}
              className="ui-select"
              value={value.barDate}
              disabled={disabled}
              onChange={(event) => pick({ barDate: event.target.value })}
            >
              {candles.map((candle) => (
                <option key={candle.start} value={candle.start}>
                  {candle.start}
                </option>
              ))}
            </select>
          </div>
          <div className="anchor-row__field">
            <label htmlFor={`${id}-field`}>{t("chart.anchor.field")}</label>
            <select
              id={`${id}-field`}
              className="ui-select"
              value={value.field}
              disabled={disabled}
              onChange={(event) => pick({ field: event.target.value as CandleField })}
            >
              {CANDLE_FIELDS.map((field) => (
                <option key={field} value={field}>
                  {t(`chart.field.${field}`)}
                </option>
              ))}
            </select>
          </div>
        </>
      )}
      <div className="anchor-row__field">
        <label htmlFor={`${id}-price`}>{t("chart.anchor.price")}</label>
        <input
          id={`${id}-price`}
          className="ui-input"
          type="number"
          inputMode="decimal"
          step="any"
          min="0"
          value={value.price}
          disabled={disabled}
          onChange={(event) => onChange({ ...value, price: event.target.value }, false)}
          onBlur={() => onChange(value, true)}
          onKeyDown={(event) => {
            if (event.key === "Enter") onChange(value, true);
          }}
        />
      </div>
    </div>
  );
};
