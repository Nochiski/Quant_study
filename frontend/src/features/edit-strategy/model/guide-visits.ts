/**
 * 새 전략 안내에서 사용자가 지나간 단계(완료 판정의 "방문"). 초안 하나에 묶인 화면 상태라 문서·URL 이 아니라
 * 탭 단위 보관함에 초안 id 로 둔다 — 편집기에 다녀오거나 새로고침해도 진행률이 문서 변화 없이 뒤로 가지 않는다.
 * 보관함이 없거나 거절되면 이 화면 동안만 기억한다.
 */
import { browserSessionStorage } from "../../../shared/lib/browser-storage";

const visitsKey = (draftId: string) => `strategy-guide.visited.${draftId}`;

export const readGuideVisits = (draftId: string): ReadonlySet<string> => {
  try {
    const raw = browserSessionStorage()?.getItem(visitsKey(draftId));
    const parsed: unknown = raw === null || raw === undefined ? [] : JSON.parse(raw);
    return new Set(
      Array.isArray(parsed)
        ? parsed.filter((value): value is string => typeof value === "string")
        : [],
    );
  } catch {
    return new Set();
  }
};

export const writeGuideVisits = (draftId: string, visited: ReadonlySet<string>): void => {
  try {
    browserSessionStorage()?.setItem(visitsKey(draftId), JSON.stringify([...visited]));
  } catch {
    // 보관함이 가득 찼거나 거절됐다. 이 화면 동안의 기억으로 충분하다.
  }
};

/** 초안이 전략으로 저장되면 그 초안의 방문 기록을 지운다. */
export const clearGuideVisits = (draftId: string): void => {
  try {
    browserSessionStorage()?.removeItem(visitsKey(draftId));
  } catch {
    // 지우지 못해도 다음에 같은 초안 id 가 쓰이지 않으므로 해가 없다.
  }
};
