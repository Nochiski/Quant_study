/**
 * 장 날짜(`YYYY-MM-DD`, 거래소 달력의 날)를 화면 표기로. 시간대를 끼우지 않는다 — 장 날짜는 시각이 아니다.
 */
const parts = (
  session: string,
): { month: string; day: string; date: Date } | null => {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/u.exec(session);
  if (match === null) return null;
  const [, year, month, day] = match;
  return {
    month,
    day,
    date: new Date(Date.UTC(Number(year), Number(month) - 1, Number(day))),
  };
};

/** `10-30(금)`. 읽지 못하면 원문. */
export const sessionLabel = (session: string): string => {
  const parsed = parts(session);
  if (parsed === null) return session;
  const weekday = new Intl.DateTimeFormat("ko-KR", {
    weekday: "short",
    timeZone: "UTC",
  }).format(parsed.date);
  return `${parsed.month}-${parsed.day}(${weekday})`;
};

/** `10-30`. 표·짧은 자리용. */
export const sessionShort = (session: string): string => {
  const parsed = parts(session);
  return parsed === null ? session : `${parsed.month}-${parsed.day}`;
};

/** 장 날짜가 오늘(보는 사람의 달력) 기준 며칠 전인가. 휴장일을 세지 않는 달력일이다. */
export const daysBefore = (session: string, today: Date): number | null => {
  const parsed = parts(session);
  if (parsed === null) return null;
  const local = Date.UTC(
    today.getFullYear(),
    today.getMonth(),
    today.getDate(),
  );
  return Math.round((local - parsed.date.valueOf()) / 86_400_000);
};
