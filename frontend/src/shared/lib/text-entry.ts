/** 이벤트 대상이 글을 쓰는 칸(입력·글 상자·선택·contenteditable)인가 — 그 칸의 단축키(Ctrl+Z·Esc)는 화면 단축키로 가로채지 않는다. */
export const isTextEntry = (target: EventTarget | null): boolean =>
  target instanceof HTMLElement &&
  (target.isContentEditable ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    (target instanceof HTMLInputElement && !["checkbox", "radio", "button"].includes(target.type)));
