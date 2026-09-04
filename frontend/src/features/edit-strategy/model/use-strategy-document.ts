import { useCallback, useEffect, useReducer } from "react";

import { parseSource, type SourceFormat } from "../../../shared/lib/yaml12";
import {
  documentReducer,
  initialDocumentState,
  shouldParse,
  type DocumentAction,
  type DocumentState,
} from "./document-state";

const PARSE_DELAY_MS = 150;

/**
 * Binds the document reducer to the parser. Parsing is debounced and skipped while the editor
 * reports an active IME composition (`view.composing`, editor ADR D2). The backend compile step
 * (P3-04) subscribes to `shouldCompile` and dispatches `compiled` the same way.
 */
export const useStrategyDocument = (
  format: SourceFormat = "yaml",
  source = "",
) => {
  const [state, dispatch] = useReducer(documentReducer, undefined, () =>
    initialDocumentState(format, source),
  );

  useEffect(() => {
    if (!shouldParse(state)) return;
    const version = state.sourceVersion;
    const text = state.source;
    const current = state.format;
    const timer = setTimeout(() => {
      dispatch({ type: "parsed", version, result: parseSource(text, current) });
    }, PARSE_DELAY_MS);
    return () => clearTimeout(timer);
  }, [state]);

  const send = useCallback((action: DocumentAction) => dispatch(action), []);
  return [state, send] as const satisfies readonly [
    DocumentState,
    (a: DocumentAction) => void,
  ];
};
