import { useMemo } from "react";

import { currentDiagnostics, type DocumentState } from "./document-state";
import { projectForm, type FormProjection } from "./form-projection";
import type { JsonSchema } from "./schema-navigator";

export type FormProjectionState = {
  /** runtime schema가 없으면 null. */
  projection: FormProjection | null;
  /**
   * 현재 텍스트의 parse가 **실패**해 같은 문서의 마지막 유효 parse로 그렸다(컨트롤은 잠긴다). parse가 아직
   * 끝나지 않은 디바운스 구간은 stale이 아니다 — 마지막 유효 parse를 조용히 그린다(리뷰 DEFECT-P404-001).
   */
  stale: boolean;
  /** 삭제 가드가 참조를 찾을 tree(projection과 같은 parse). */
  tree: unknown;
};

/**
 * Form 패널 입력(WORKFLOW P4-04): 현재 parse가 유효하면 그것으로, 구문 오류면 같은 문서의 마지막 유효
 * parse로 STALE 표시와 함께 그린다. 진단은 현재 compile의 것만 붙인다(stale일 때는 비어 있다).
 */
export const useFormProjection = (
  state: DocumentState,
  schema: JsonSchema | null,
): FormProjectionState =>
  useMemo(() => {
    const parsedCurrent =
      state.parse !== null && state.parsedVersion === state.sourceVersion
        ? state.parse
        : null;
    const current =
      parsedCurrent !== null && parsedCurrent.status === "ok"
        ? parsedCurrent
        : null;
    // "parse 실패"와 "parse 대기"를 구분한다: 대기 중에는 배지·문구 없이 마지막 유효 parse를 그린다.
    const failed = parsedCurrent !== null && current === null;
    const parse = current ?? state.lastValidParse?.result ?? null;
    const stale = failed && parse !== null;
    const tree = parse !== null ? parse.tree : {};
    if (schema === null) return { projection: null, stale, tree };
    return {
      projection: projectForm(
        schema,
        parse,
        stale ? [] : currentDiagnostics(state),
      ),
      stale,
      tree,
    };
  }, [schema, state]);
