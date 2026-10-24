/**
 * 보유·매매 화면의 배치 계산. 값(보유·손익·구분)은 backend 투영이 소유하고, 여기서는 그 값을 어디에 그릴지만
 * 정한다.
 */

/** 고른 날 이하에서 가장 늦은 매매일의 위치. 첫 매매일보다 이르면 0이다. 목록은 오름차순이다. */
export const sessionPosition = (sessions: readonly string[], day: string): number => {
  let found = 0;
  for (let index = 0; index < sessions.length; index += 1) {
    if (sessions[index] > day) break;
    found = index;
  }
  return found;
};

const DAY_MS = 86_400_000;
const dayNumber = (day: string) => Date.parse(`${day}T00:00:00Z`) / DAY_MS;

/**
 * 실행 기간(`first`~`last`, 양끝 포함) 위의 보유 구간 막대 위치(백분율). 하루짜리 구간도 하루 폭을 갖는다.
 */
export const periodBar = (
  period: { start: string; end: string },
  first: string,
  last: string,
): { left: number; width: number } => {
  const origin = dayNumber(first);
  const total = dayNumber(last) - origin + 1;
  const left = ((dayNumber(period.start) - origin) / total) * 100;
  const width = ((dayNumber(period.end) - dayNumber(period.start) + 1) / total) * 100;
  return { left, width };
};
