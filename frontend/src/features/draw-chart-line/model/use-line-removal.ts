import { useLayoutEffect, useState } from "react";

import {
  useCreateChartLine,
  useDeleteChartLine,
  type ChartLineBody,
} from "../../../entities/chart";
import { useCommittedRef } from "../../../shared/lib/react";
import { popUndo, pushUndo, type UndoStack } from "./line-edit";

export type RemovalNotice = { kind: "deleted"; title: string } | { kind: "restored" };

/** 글을 쓰는 칸(입력·메모)의 Ctrl+Z는 그 칸의 되돌리기다 — 지운 선 되살리기로 가로채지 않는다. */
const isTextEntry = (target: EventTarget | null): boolean =>
  target instanceof HTMLElement &&
  (target.isContentEditable ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    (target instanceof HTMLInputElement && !["checkbox", "radio", "button"].includes(target.type)));

/**
 * 선 지우기와 되돌리기. 지우면 같은 내용으로 다시 만들 본문을 쌓아 두고(로컬 UI 상태), 되돌리기 알림의 버튼이나
 * Ctrl+Z(⌘Z)가 마지막에 지운 선을 다시 만든다. 다시 만든 선은 새 id를 받는다.
 *
 * 키 리스너는 한 번만 달고 지금 쌓인 것은 commit과 같은 시점의 ref로 읽는다(`frontend-react-effects.md`).
 */
export const useLineRemoval = () => {
  const [stack, setStack] = useState<UndoStack>([]);
  const [notice, setNotice] = useState<RemovalNotice | null>(null);
  const remove = useDeleteChartLine();
  const create = useCreateChartLine();

  const removeLine = (line: ChartLineBody, title: string) =>
    remove.mutate(line, {
      onSuccess: () => {
        setStack((current) => pushUndo(current, line));
        setNotice({ kind: "deleted", title });
      },
    });

  const undo = (): boolean => {
    const [body, rest] = popUndo(stack);
    if (body === null || create.isPending) return false;
    setStack(rest);
    create.mutate(body, {
      onSuccess: () => setNotice({ kind: "restored" }),
      // 되살리지 못했으면 다시 해 볼 수 있게 돌려 둔다
      onError: () => setStack((current) => [...current, body]),
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
