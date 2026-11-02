/**
 * backend 가 보낸 시각(ISO 8601, UTC)을 화면 시각으로. 보는 사람의 PC 시간대로 보이고 시간대 이름(KST 등)을
 * 함께 붙여 어느 기준인지 드러낸다. 읽지 못한 값은 원문 그대로 둔다. `seconds` 는 같은 분 안의 순서가 중요한
 * 기록(감사 기록)이 쓴다. `timeZone` 을 주면 그 시간대로 보인다(기본은 브라우저 시간대).
 */
export const displayTime = (
  value: string,
  { seconds = false, timeZone }: { seconds?: boolean; timeZone?: string } = {},
): string => {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? value
    : new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        ...(seconds ? { second: "2-digit" } : {}),
        ...(timeZone === undefined ? {} : { timeZone }),
        timeZoneName: "short",
      }).format(parsed);
};
