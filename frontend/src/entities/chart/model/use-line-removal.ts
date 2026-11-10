import { useLayoutEffect, useRef, useState } from "react";

import type { ChartLineBody } from "../../../shared/api";
import { useCommittedRef } from "../../../shared/lib/react";
import { isTextEntry } from "../../../shared/lib/text-entry";
import { useCreateChartLine, useDeleteChartLine, type ChartTarget } from "./chart-queries";
import { popUndo, pushUndo, type UndoStack } from "./undo-stack";

/** `lost` = 지금 그릴 수 없어 되돌리기에 쌓지 못한 선이 있는가, `undoable` = 이 지우기를 되돌릴 수 있는가. */
export type RemovalNotice =
  | { kind: "deleted"; title: string; lost: boolean; undoable: boolean }
  | { kind: "restored"; title: string };

/**
 * 선 지우기와 되돌리기 — 수동 그리기(내가 그은 선)와 자동 찾기(자동 후보 칸)가 함께 쓴다. 지우면 같은 내용으로 다시 만들 본문을 쌓아 두고(로컬 UI 상태), 되돌리기 알림의 버튼이나
 * Ctrl+Z(⌘Z)가 마지막에 지운 선을 다시 만든다. 다시 만든 선은 새 id를 받는다.
 *
 * - 지금 그릴 수 없는 선(`issue`)은 쌓지 않는다 — 다시 만들기는 새 선처럼 기준점을 계열로 검사해 거절되므로, 쌓아
 *   두면 되돌리기가 조용히 실패한다. 지울 때 알림이 되돌릴 수 없다고 바로 말한다.
 *
 * - 쌓기와 알림은 그 종목의 것이다 — 다른 종목으로 넘어가면 비운다(다른 종목 화면에서 앞 종목의 선을 되살리지
 *   않는다). 지우기 응답이 종목을 바꾼 뒤에 와도 새 종목의 쌓기에 넣지 않는다(요청 때의 종목과 견준다).
 * - 지우는 요청이 나가 있는 동안 다음 지우기는 받지 않는다(Delete 키 연타). 결과 처리는 `mutateAsync`의 약속으로
 *   해서 겹친 호출이 앞 호출의 처리를 덮지 않는다.
 * - 키 리스너는 한 번만 달고 지금 쌓인 것은 commit과 같은 시점의 ref로 읽는다(`frontend-react-effects.md`).
 */
export const useLineRemoval = (target: ChartTarget) => {
  const [stack, setStack] = useState<UndoStack>([]);
  const [notice, setNotice] = useState<RemovalNotice | null>(null);
  const scopeKey = `${target.kind}|${target.code}`;
  const [scope, setScope] = useState(scopeKey);
  if (scope !== scopeKey) {
    // 렌더 중 상태 맞추기: 다른 종목으로 넘어가면 되돌리기 쌓기와 알림을 버린다
    setScope(scopeKey);
    setStack([]);
    setNotice(null);
  }
  // 응답(약속) 처리는 React 밖에서 불린다 — 그때의 종목은 commit과 같은 시점의 ref로 읽는다
  const scopeRef = useCommittedRef(scopeKey);
  const remove = useDeleteChartLine();
  const create = useCreateChartLine();
  // 지우는 요청이 나가 있는가. 변이 상태(`isPending`)는 다음 렌더에야 바뀌어 같은 틈의 연타를 못 막으므로
  // 부르는 순간 세운다.
  const removing = useRef(false);

  /**
   * 지운다(선 하나, 또는 함께 지울 묶음 — 자동 수평 구간 한 벌). 묶음은 되돌리기 한 번에 함께 되살아난다. 이미 지우는
   * 중이면 받지 않고 false. 묶음 중 일부만 지워졌으면 지워진 것만 되돌리기에 쌓는다.
   */
  const removeLine = (group: readonly ChartLineBody[], title: string): boolean => {
    if (removing.current) return false;
    removing.current = true;
    const requested = scopeKey;
    Promise.allSettled(group.map((line) => remove.mutateAsync(line)))
      .then((results) => {
        const removed = group.filter((_, index) => results[index].status === "fulfilled");
        if (removed.length === 0 || scopeRef.current !== requested) return;
        const restorable = removed.filter((line) => line.issue === null);
        if (restorable.length > 0) setStack((current) => pushUndo(current, restorable, title));
        setNotice({
          kind: "deleted",
          title,
          lost: restorable.length < removed.length,
          undoable: restorable.length > 0,
        });
      })
      .finally(() => {
        removing.current = false;
      });
    return true;
  };

  // 되살리는 요청이 나가 있는가(Ctrl+Z 연타가 같은 선을 두 번 되살리지 않게, 지우기와 같은 까닭으로 ref).
  const restoring = useRef(false);

  const undo = (): boolean => {
    const [entry, rest] = popUndo(stack);
    if (entry === null || restoring.current) return false;
    restoring.current = true;
    setStack(rest);
    Promise.allSettled(entry.bodies.map((body) => create.mutateAsync(body)))
      .then((results) => {
        // 되살리지 못한 것은 다시 해 볼 수 있게 돌려 둔다
        const failed = entry.bodies.filter((_, index) => results[index].status === "rejected");
        if (failed.length > 0) setStack((current) => [...current, { title: entry.title, bodies: failed }]);
        else setNotice({ kind: "restored", title: entry.title });
      })
      .finally(() => {
        restoring.current = false;
      });
    return true;
  };

  const undoRef = useCommittedRef(undo);
  useLayoutEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.shiftKey || event.altKey) return;
      if (event.key.toLowerCase() !== "z" || isTextEntry(event.target)) return;
      if (undoRef.current()) event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undoRef]);

  return {
    removeLine,
    undo,
    canUndo: stack.length > 0,
    notice,
    dismissNotice: () => setNotice(null),
    error: remove.error ?? create.error,
    removing: remove.isPending,
  };
};

export type LineRemoval = ReturnType<typeof useLineRemoval>;
