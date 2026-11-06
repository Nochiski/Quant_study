import { tFill } from "../../../shared/config";

const EOK = 100_000_000;
const JO = 10_000 * EOK;

/**
 * 원 금액의 크기를 조·억으로 읽기 쉽게(부호 없음 — 부호는 `SignedValue` 가 붙인다).
 *
 * 1조 이상은 조 단위 소수(10조 미만 둘째 자리, 그 위 첫째 자리), 그 밑은 억 단위 정수로 반올림한다.
 * 반올림은 여기서 한 번만 한다 — backend 는 원 단위 원값 합을 준다.
 */
export const shortWon = (amountKrw: number): string => {
  const magnitude = Math.abs(amountKrw);
  // 억으로 먼저 반올림하고 그 값으로 조·억을 고른다 — 9,999.5억이 "10,000억"이 되지 않게
  // (backend 요약 문장의 `won_words` 와 같은 경계).
  const eok = Math.round(magnitude / EOK);
  if (eok >= JO / EOK) {
    const jo = magnitude / JO;
    // 둘째 자리로 올림하면 10조가 되는 값(9.995조)은 10조 위의 표기(첫째 자리)를 쓴다.
    const value = jo.toFixed(2) === "10.00" || jo >= 10 ? jo.toFixed(1) : jo.toFixed(2);
    return tFill("market.money.jo", { value });
  }
  if (eok === 0 && magnitude > 0) return tFill("market.money.belowEok", {});
  return tFill("market.money.eok", { value: eok.toLocaleString("ko-KR") });
};

/** 표·툴팁에 쓰는 억원 정수(부호 없음). */
export const eokWon = (amountKrw: number): string =>
  Math.round(Math.abs(amountKrw) / EOK).toLocaleString("ko-KR");

/** 유동시총처럼 큰 금액의 조원(소수 첫째 자리, 부호 없음). */
export const joWon = (amountKrw: number): string =>
  (Math.abs(amountKrw) / JO).toLocaleString("ko-KR", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });

/** 등락률 크기(부호 없음, 둘째 자리). */
export const percentMagnitude = (percent: number): string =>
  `${Math.abs(percent).toFixed(2)}%`;
