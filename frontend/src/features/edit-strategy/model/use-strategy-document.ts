import { useCallback, useEffect, useReducer, useState } from "react";

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
  const [loadedKey, setLoadedKey] = useState(key);
  // 같은 route의 다른 문서는 새 제목과 이전 투영이 함께 commit되기 전에 reducer에서 교체한다.
  // 저장 응답 뒤 URL만 현재 base를 따라잡는 경우는 편집·undo를 보존한다(#419).
  if (loadedKey !== key) {
    setLoadedKey(key);
    const alreadyBase =
      source.kind === "revision" &&
      state.strategyId === source.document.strategy_id &&
      state.baseRevision === source.document.revision;
    if (!alreadyBase) dispatch(loadAction(source));
  }

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
