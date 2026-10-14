/**
 * backend 가 보낸 시각(ISO 8601, UTC)을 화면 시각으로. 서버 시각과 맞춰 보도록 UTC 로 보이고, 읽지 못한
 * 값은 원문 그대로 둔다. `seconds` 는 같은 분 안의 순서가 중요한 기록(감사 기록)이 쓴다.
 */
export const displayTime = (
  value: string,
  { seconds = false }: { seconds?: boolean } = {},
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
        timeZone: "UTC",
        timeZoneName: "short",
      }).format(parsed);
};
