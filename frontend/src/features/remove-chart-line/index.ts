/**
 * 개별종목차트 선 지우기와 되돌리기(사용자 행동)의 입구 — 수동 그리기(내가 그은 선)와 자동 찾기(자동 후보 칸)가
 * 함께 쓴다. 위젯이 `useLineRemoval`을 한 번 만들고 두 칸에 지우기 콜백을, 차트 카드에 `UndoNotice`를 준다.
 */
export { UNDO_LIMIT, popUndo, pushUndo, recreateChartLine, type UndoEntry, type UndoStack } from "./model/undo-stack";
export { useLineRemoval, type LineRemoval, type RemovalNotice } from "./model/use-line-removal";
export { UndoNotice } from "./ui/undo-notice";
