/**
 * AI 어시스턴트 창(FloatingWindow)의 위치·크기 보관함 키. 새 전략 안내·새 전략 편집기·리비전 편집기가 같은 키를
 * 써서, 화면을 옮겨도 같은 창 하나가 같은 자리·크기로 뜬다. 열림 상태는 화면마다 다르다(편집기는 패널 배치가,
 * 안내는 화면이 소유한다).
 */
export const ASSISTANT_WINDOW_STORAGE_KEY = "quant.assistant-window.v1";
