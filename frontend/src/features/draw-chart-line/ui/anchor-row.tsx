import { memo, useId, type KeyboardEvent } from "react";

import {
  CANDLE_FIELDS,
  type CandleField,
  type ChartCandleBody,
  type InstrumentKind,
} from "../../../entities/chart";
import { t } from "../../../shared/config";
import { withBar, type AnchorDraft } from "../model/anchor-draft";

/** 봉 날짜 목록. 봉이 수천 개일 수 있어 목록이 같으면 다시 그리지 않는다(끄는 동안 카드가 다시 그려진다). */
const DateOptions = memo(function DateOptions({ candles }: { candles: readonly ChartCandleBody[] }) {
  return candles.map((candle) => (
    <option key={candle.start} value={candle.start}>
      {candle.start}
    </option>
  ));
});

type AnchorRowProps = {
  /** 줄 이름(예: "먼저 점 (1)"). 칸 묶음의 접근 이름이다. */
  name: string;
  value: AnchorDraft;
  /** 고를 수 있는 봉(완성 봉). */
  candles: readonly ChartCandleBody[];
  instrumentKind: InstrumentKind;
  disabled?: boolean;
  /**
   * `commit`은 고르기가 끝났다는 뜻이다 — select·가격 칸에서 나가거나 Enter. 키보드로 select를 훑는 동안(값이
   * 바뀔 때마다)은 저장하지 않는다.
   */
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
    onChange(withBar(value, candles, instrumentKind, change), false);
  const commitOnEnter = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Enter") onChange(value, true);
  };
  return (
    <div className="anchor-row" role="group" aria-label={name}>
      <span className="anchor-row__name" aria-hidden="true">
        {name}
      </span>
      <div className="anchor-row__field">
        <label htmlFor={`${id}-date`}>{t("chart.anchor.date")}</label>
        <select
          id={`${id}-date`}
          className="ui-select"
          value={value.barDate}
          disabled={disabled}
          onChange={(event) => pick({ barDate: event.target.value })}
          onBlur={() => onChange(value, true)}
          onKeyDown={commitOnEnter}
        >
          <DateOptions candles={candles} />
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
          onBlur={() => onChange(value, true)}
          onKeyDown={commitOnEnter}
        >
          {CANDLE_FIELDS.map((field) => (
            <option key={field} value={field}>
              {t(`chart.field.${field}`)}
            </option>
          ))}
        </select>
      </div>
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
          onKeyDown={commitOnEnter}
        />
      </div>
    </div>
  );
};
