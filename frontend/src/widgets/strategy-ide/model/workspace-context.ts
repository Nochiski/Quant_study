import { createContext } from "react";

/** 실행계획의 명시적 원문 이동만 편집 공간으로 돌아간다. 추적 노드 선택은 현재 작업을 유지한다. */
export const OpenEditorWorkspaceContext = createContext<(() => void) | null>(null);
