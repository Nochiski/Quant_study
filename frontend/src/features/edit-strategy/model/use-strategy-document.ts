import { useCallback, useEffect, useReducer, useRef } from "react";

import { parseSource } from "../../../shared/lib/yaml12";
import {
  documentSourceKey,
  loadAction,
  type DocumentSource,
} from "./document-source";
import {
  documentReducer,
  initialDocumentState,
  shouldParse,
  type DocumentAction,
  type DocumentState,
} from "./document-state";

const PARSE_DELAY_MS = 150;

/**
 * Binds the document reducer to its source and to the parser. The reducer is loaded from the
 * source once and re-loaded only when the source identity changes (another revision URL), so an
 * edit in progress survives unrelated re-renders. A revision that the reducer already holds as
 * its base (the URL catching up after a save) is not re-loaded either. Parsing is debounced and
 * skipped while the editor reports an active IME composition (`view.composing`, editor ADR D2).
 * The backend compile step (P3-04) subscribes to `shouldCompile` and dispatches `compiled`.
 */
export const useStrategyDocument = (source: DocumentSource) => {
  const [state, dispatch] = useReducer(documentReducer, source, (initial) =>
    documentReducer(initialDocumentState(), loadAction(initial)),
  );

  const key = documentSourceKey(source);
  const loadedKey = useRef(key);
  useEffect(() => {
    if (loadedKey.current === key) return;
    loadedKey.current = key;
    const alreadyBase =
      source.kind === "revision" &&
      state.strategyId === source.document.strategy_id &&
      state.baseRevision === source.document.revision;
    if (!alreadyBase) dispatch(loadAction(source));
  }, [key, source, state.strategyId, state.baseRevision]);

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
