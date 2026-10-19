import type { HTMLAttributes } from "react";

import { t } from "../config";

const DIRECTION_LABEL = {
  up: "ui.signed.up",
  down: "ui.signed.down",
} as const;

type SignedValueProps = HTMLAttributes<HTMLSpanElement> & {
  /** 부호와 색을 정하는 원래 값. 0·NaN은 방향 없음이다. */
  value: number;
  /** 부호 없이 서식을 마친 크기(예: `12.30%`). 서식은 호출자가 소유한다. */
  magnitude: string;
};

/** 수익 방향을 부호·색·낭독 문구로 함께 보인다. 어떤 값에 쓸지는 호출자가 정한다. */
export const SignedValue = ({
  value,
  magnitude,
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
        <span className="sr-only">{t(DIRECTION_LABEL[direction])} </span>
      ) : null}
      {direction === "up" ? "+" : direction === "down" ? "−" : ""}
      {magnitude}
    </span>
  );
};
