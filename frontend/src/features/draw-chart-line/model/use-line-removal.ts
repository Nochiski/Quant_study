import { useLayoutEffect, useRef, useState } from "react";

import {
  useCreateChartLine,
  useDeleteChartLine,
  type ChartLineBody,
  type ChartTarget,
} from "../../../entities/chart";
import { useCommittedRef } from "../../../shared/lib/react";
import { isTextEntry, popUndo, pushUndo, type UndoStack } from "./line-edit";

export type RemovalNotice = { kind: "deleted"; title: string } | { kind: "restored" };

/**
 * 선 지우기와 되돌리기. 지우면 같은 내용으로 다시 만들 본문을 쌓아 두고(로컬 UI 상태), 되돌리기 알림의 버튼이나
 * Ctrl+Z(⌘Z)가 마지막에 지운 선을 다시 만든다. 다시 만든 선은 새 id를 받는다.
 *
 * - 쌓기와 알림은 그 종목의 것이다 — 다른 종목으로 넘어가면 비운다(다른 종목 화면에서 앞 종목의 선을 되살리지
 *   않는다).
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
  const remove = useDeleteChartLine();
  const create = useCreateChartLine();
  // 지우는 요청이 나가 있는가. 변이 상태(`isPending`)는 다음 렌더에야 바뀌어 같은 틈의 연타를 못 막으므로
  // 부르는 순간 세운다.
  const removing = useRef(false);

  /** 지운다. 이미 지우는 중이면 받지 않고 false. */
  const removeLine = (line: ChartLineBody, title: string): boolean => {
    if (removing.current) return false;
    removing.current = true;
    remove
      .mutateAsync(line)
      .then(
        () => {
          setStack((current) => pushUndo(current, line));
          setNotice({ kind: "deleted", title });
        },
        () => undefined,
      )
      .finally(() => {
        removing.current = false;
      });
    return true;
  };

  // 되살리는 요청이 나가 있는가(Ctrl+Z 연타가 같은 선을 두 번 되살리지 않게, 지우기와 같은 까닭으로 ref).
  const restoring = useRef(false);

  const undo = (): boolean => {
    const [body, rest] = popUndo(stack);
    if (body === null || restoring.current) return false;
    restoring.current = true;
    setStack(rest);
    create
      .mutateAsync(body)
      .then(
        () => setNotice({ kind: "restored" }),
        // 되살리지 못했으면 다시 해 볼 수 있게 돌려 둔다
        () => setStack((current) => [...current, body]),
      )
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
