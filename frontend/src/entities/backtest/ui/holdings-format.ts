import type { HoldingChange, SessionHoldingsReport } from "../../../shared/api";
import { t, tFill, tOptional } from "../../../shared/config";

export type HoldingRowView = NonNullable<SessionHoldingsReport["holdings"]>["holdings"][number];
type SecurityLabelView = HoldingRowView["security"];

/** 보유·매매 표의 서식. 값은 backend 투영 그대로이고 자릿수·부호 표기만 정한다. */
export const holdingMoney = (value: number) =>
  `₩${Math.round(value).toLocaleString("ko-KR")}`;

/** 부호 없는 크기. 부호는 `SignedValue` 가 붙인다. */
export const holdingPercent = (value: number) => `${(Math.abs(value) * 100).toFixed(1)}%`;

/** 비중처럼 수익 방향이 아닌 부호 있는 비율. 숏 비중·음수 현금은 "−" 가 붙고 색은 없다. */
export const holdingWeight = (value: number) => `${value < 0 ? "−" : ""}${holdingPercent(value)}`;

/** 체결가는 슬리피지가 반영된 소수 가격이라 소수 둘째 자리까지 보인다. */
export const holdingPrice = (value: number) =>
  `₩${value.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}`;

export const holdingShares = (quantity: string) =>
  tFill("backtest.holdings.shares", { quantity: Number(quantity).toLocaleString("ko-KR") });

export const securityName = (security: SecurityLabelView) => security.name ?? security.security_id;

/** 이름을 모르는 종목은 이름 자리에 id 를 쓰므로 코드 칸을 비운다. */
export const securityCode = (security: SecurityLabelView) =>
  security.name === null ? null : (security.ticker ?? security.security_id);

export const changeLabel = (change: HoldingChange) => t(`backtest.holdings.change.${change}`);

/** 체결 순수량이 있는 늘림·줄임만 수량을 붙인다(예: "늘림 +5주"). */
export const changeText = (change: HoldingChange, filledQuantity: string | null) => {
  if (filledQuantity === null || (change !== "added" && change !== "reduced"))
    return changeLabel(change);
  const quantity = Number(filledQuantity);
  const magnitude = Math.abs(quantity).toLocaleString("ko-KR");
  return tFill("backtest.holdings.change.quantity", {
    change: changeLabel(change),
    quantity: `${quantity < 0 ? "−" : "+"}${magnitude}`,
  });
};

export const sideLabel = (side: string) => tOptional(`backtest.holdings.side.${side}`) ?? side;
