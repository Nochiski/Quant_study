import type { HTMLAttributes } from "react";

import { t } from "../config";

// 낭독 문구 짝. 수익 방향(상승·하락)과 매매 방향(순매수·순매도)은 같은 색을 쓰되 읽는 말이 다르다.
const DIRECTION_LABEL = {
  return: { up: "ui.signed.up", down: "ui.signed.down" },
  flow: { up: "ui.signed.netBuy", down: "ui.signed.netSell" },
} as const;

type SignedValueProps = HTMLAttributes<HTMLSpanElement> & {
  /** 부호와 색을 정하는 원래 값. 0·NaN은 방향 없음이다. */
  value: number;
  /** 부호 없이 서식을 마친 크기(예: `12.30%`). 서식은 호출자가 소유한다. */
  magnitude: string;
  /** 낭독 문구 짝. 수익률은 `return`(기본), 순매수 금액·매매 구분은 `flow`. */
  kind?: keyof typeof DIRECTION_LABEL;
};

/** 수익·매매 방향을 부호·색·낭독 문구로 함께 보인다. 어떤 값에 쓸지는 호출자가 정한다. */
export const SignedValue = ({
  value,
  magnitude,
  kind = "return",
  className = "",
  ...props
}: SignedValueProps) => {
  const direction = value > 0 ? "up" : value < 0 ? "down" : null;
  return (
    <span
      className={`ui-signed${direction ? ` ui-signed--${direction}` : ""} ${className}`.trim()}
      {...props}
    >
      {direction ? (
        <span className="sr-only">{t(DIRECTION_LABEL[kind][direction])} </span>
      ) : null}
      {direction === "up" ? "+" : direction === "down" ? "−" : ""}
      {magnitude}
    </span>
  );
};
