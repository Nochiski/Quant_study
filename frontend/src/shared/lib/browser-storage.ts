/** 브라우저 보관함이 없거나 접근이 거절되면 선택적 로컬 복원을 끈다. */
export const browserStorage = (): Storage | null => {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
};

/** 탭 단위 보관함(새로고침에는 남고 탭을 닫으면 사라진다). 없거나 거절되면 null 이다. */
export const browserSessionStorage = (): Storage | null => {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
};
