import { useId } from "react";

import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import type { DocumentHistory } from "../model/use-document-history";
import "./document-history.css";

/**
 * 되돌리기·다시 실행 버튼(WORKFLOW P1-02, spec D9). 탭 목록 줄(탭 패널 밖)에 살아서 Graph·Form 탭에
 * 머문 채로도 누를 수 있다 — 편집기는 그 탭에서 `hidden`이라 포커스가 없고, CodeMirror 키맵은 닿지 않는다.
 *
 * 비활성은 `disabled`가 아니라 `aria-disabled`다: 키보드 사용자가 버튼까지 이동해 이유를 읽을 수 있어야
 * 하고(포커스 불가 버튼은 읽히지 않는다), 깊이가 한 프레임 늦어도 클릭이 조용히 버려지지 않는다. 이유는
 * 툴팁이 아니라 화면에 남는 문장이고 `aria-describedby`로 두 버튼에 붙는다.
 */
export const DocumentHistoryActions = ({
  history,
}: {
  history: DocumentHistory;
}) => {
  const hintId = useId();
  const { depth, undo, redo } = history;
  const noUndo = depth.undo === 0;
  const noRedo = depth.redo === 0;
  const hint = noUndo && noRedo
    ? t("ide.history.empty")
    : noUndo
      ? t("ide.undo.empty")
      : noRedo
        ? t("ide.redo.empty")
        : null;
  const describedBy = hint === null ? undefined : hintId;
  return (
    <div className="doc-history" role="group" aria-label={t("ide.history")}>
      <Button
        size="small"
        onClick={() => undo()}
        aria-disabled={noUndo || undefined}
        aria-describedby={noUndo ? describedBy : undefined}
        aria-keyshortcuts="Control+Z Meta+Z"
      >
        {t("ide.undo")}
      </Button>
      <Button
        size="small"
        onClick={() => redo()}
        aria-disabled={noRedo || undefined}
        aria-describedby={noRedo ? describedBy : undefined}
        aria-keyshortcuts="Control+Shift+Z Meta+Shift+Z"
      >
        {t("ide.redo")}
      </Button>
      {hint === null ? null : (
        <span id={hintId} className="doc-history__hint">
          {hint}
        </span>
      )}
    </div>
  );
};
