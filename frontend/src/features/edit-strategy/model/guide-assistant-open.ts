/**
 * 새 전략 안내의 AI 도우미 창을 열어 두었는가(UI 로컬 상태). 사용자가 열거나 최소화한 마지막 상태를 브라우저
 * 보관함에 남겨 다음 안내에서도 그대로 연다. 기록이 없으면 null — 화면이 공급자 연결 여부로 정한다.
 */
import { browserStorage } from "../../../shared/lib/browser-storage";

const KEY = "strategy-guide.assistant-open.v1";

export const readGuideAssistantOpen = (): boolean | null => {
  try {
    const raw = browserStorage()?.getItem(KEY);
    return raw === "true" ? true : raw === "false" ? false : null;
  } catch {
    return null;
  }
};

export const writeGuideAssistantOpen = (open: boolean): void => {
  try {
    browserStorage()?.setItem(KEY, String(open));
  } catch {
    // 보관함이 없거나 거절됐다. 이 화면 동안만 기억한다.
  }
};
