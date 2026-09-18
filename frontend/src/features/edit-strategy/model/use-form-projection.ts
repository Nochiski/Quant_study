import { useMemo } from "react";

import { currentDiagnostics, type DocumentState } from "./document-state";
import { projectForm, type FormProjection } from "./form-projection";
import type { JsonSchema } from "./schema-navigator";

export type FormProjectionState = {
  /** runtime schema가 없으면 null. */
  projection: FormProjection | null;
  /** 현재 텍스트가 parse되지 않아 같은 문서의 마지막 유효 parse로 그렸다(컨트롤은 잠긴다). */
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
    const current =
      state.parse !== null &&
      state.parse.status === "ok" &&
      state.parsedVersion === state.sourceVersion
        ? state.parse
        : null;
    const parse = current ?? state.lastValidParse?.result ?? null;
    const stale = current === null && parse !== null;
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
