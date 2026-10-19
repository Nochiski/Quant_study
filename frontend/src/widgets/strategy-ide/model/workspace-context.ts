import { createContext } from "react";

/** 실행계획의 명시적 원문 이동만 편집 공간으로 돌아간다. 추적 노드 선택은 현재 작업을 유지한다. */
export const OpenEditorWorkspaceContext = createContext<(() => void) | null>(null);

/** 선정 미리보기의 "왜 골랐나"가 추적 작업공간을 연다(RD-04-C). 무엇을 추적할지는 URL 이 정한다. */
export const OpenTraceWorkspaceContext = createContext<(() => void) | null>(null);
