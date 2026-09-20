import { useId } from "react";

import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import type { DocumentHistory } from "../model/use-document-history";
import "./document-history.css";

/** 되돌리기·다시 실행 화살표. 폰트에 기대지 않아 시각 기준선이 흔들리지 않는다. */
const Arrow = ({ direction }: { direction: "undo" | "redo" }) => (
  <svg
    className="doc-history__icon"
    viewBox="0 0 16 16"
    width="12"
    height="12"
    aria-hidden="true"
    focusable="false"
  >
    <path
      d={
        direction === "undo"
          ? "M5.5 4.5 2.5 7.5l3 3M2.9 7.5h6.35a4 4 0 0 1 0 8H7"
          : "M10.5 4.5l3 3-3 3M13.1 7.5H6.75a4 4 0 0 0 0 8H9"
      }
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

/**
 * 되돌리기·다시 실행 버튼(WORKFLOW P1-02, spec D9). 탭 목록 줄(탭 패널 밖)에 살아서 Graph·Form 탭에
 * 머문 채로도 누를 수 있다 — 편집기는 그 탭에서 `hidden`이라 포커스가 없고, CodeMirror 키맵은 닿지 않는다.
 *
 * 비활성은 `disabled`가 아니라 `aria-disabled`다: 키보드 사용자가 버튼까지 이동해 이유를 읽을 수 있어야
 * 하고(포커스 불가 버튼은 읽히지 않는다), 깊이가 한 프레임 늦어도 클릭이 조용히 버려지지 않는다. 이유는
 * `title`과 `aria-describedby`가 가리키는 요소로만 전한다 — 탭 줄은 1280px에서도 한 줄이어야 해서
 * 화면에 문장을 더 둘 자리가 없다.
 */
export const DocumentHistoryActions = ({
  history,
}: {
  history: DocumentHistory;
}) => {
  const base = useId();
  const { depth, undo, redo } = history;
  const noUndo = depth.undo === 0;
  const noRedo = depth.redo === 0;
  const undoReason = noUndo ? t("ide.undo.empty") : undefined;
  const redoReason = noRedo ? t("ide.redo.empty") : undefined;
  return (
    <div
      className="doc-history"
      role="group"
      aria-label={t("ide.history")}
    >
      <Button
        size="small"
        tone="ghost"
        className="doc-history__button"
        onClick={() => undo()}
        aria-disabled={noUndo || undefined}
        aria-describedby={noUndo ? `${base}-undo` : undefined}
        title={undoReason}
        aria-keyshortcuts="Control+Z Meta+Z"
      >
        <Arrow direction="undo" />
        {t("ide.undo")}
      </Button>
      <Button
        size="small"
        tone="ghost"
        className="doc-history__button"
        onClick={() => redo()}
        aria-disabled={noRedo || undefined}
        aria-describedby={noRedo ? `${base}-redo` : undefined}
        title={redoReason}
        aria-keyshortcuts="Control+Shift+Z Meta+Shift+Z"
      >
        <Arrow direction="redo" />
        {t("ide.redo")}
      </Button>
      {/* 비활성 사유. 화면 자리를 차지하지 않지만 보조 기술에는 버튼 설명으로 읽힌다. */}
      <span id={`${base}-undo`} className="sr-only">
        {undoReason}
      </span>
      <span id={`${base}-redo`} className="sr-only">
        {redoReason}
      </span>
    </div>
  );
};
