/** 브라우저 보관함이 없거나 접근이 거절되면 선택적 로컬 복원을 끈다. */
export const browserStorage = (): Storage | null => {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
};
