import {
  autocompletion,
  type CompletionContext,
} from "@codemirror/autocomplete";
import {
  defaultKeymap,
  history,
  historyField,
  historyKeymap,
  indentWithTab,
  isolateHistory,
} from "@codemirror/commands";
import { json } from "@codemirror/lang-json";
import { yaml } from "@codemirror/lang-yaml";
import {
  bracketMatching,
  foldGutter,
  foldKeymap,
  indentOnInput,
  syntaxHighlighting,
  defaultHighlightStyle,
} from "@codemirror/language";
import {
  linter,
  lintGutter,
  lintKeymap,
  setDiagnostics,
  type Diagnostic,
} from "@codemirror/lint";
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search";
import {
  Compartment,
  EditorSelection,
  EditorState,
  type Extension,
} from "@codemirror/state";
import {
  drawSelection,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  hoverTooltip,
  keymap,
  lineNumbers,
} from "@codemirror/view";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

import type {
  CodeEditorHandle,
  CodeEditorProps,
  EditorCompletionSource,
  EditorDiagnostic,
  EditorHoverSource,
} from "./handle";
import "./code-editor.css";

const HISTORY_FIELDS = { history: historyField };

const toCmDiagnostics = (
  items: EditorDiagnostic[],
  length: number,
): Diagnostic[] =>
  items.map((item) => ({
    from: Math.max(0, Math.min(item.from, length)),
    to: Math.max(Math.min(item.from, length), Math.min(item.to, length)),
    severity: item.severity,
    message: item.message,
    source: item.code,
  }));

const toCmCompletion =
  (source: EditorCompletionSource) => async (context: CompletionContext) => {
    const result = await source({
      text: context.state.doc.toString(),
      offset: context.pos,
      explicit: context.explicit,
    });
    if (!result) return null;
    return {
      from: result.from,
      options: result.options.map((option) => ({
        label: option.label,
        detail: option.detail,
        info: option.info,
        type: option.type,
        apply: option.apply,
      })),
    };
  };

const toCmHover = (source: EditorHoverSource) =>
  hoverTooltip(
    (_view, pos) => {
      const hover = source(pos);
      if (!hover) return null;
      return {
        pos: hover.from,
        end: hover.to,
        above: true,
        create: () => {
          const dom = document.createElement("div");
          dom.className = "code-editor__hover";
          for (const line of hover.lines) {
            const row = document.createElement("div");
            row.textContent = line;
            dom.appendChild(row);
          }
          return { dom };
        },
      };
    },
    { hoverTime: 250 },
  );

/**
 * CodeMirror 6 implementation of the editor handle (loaded lazily by `CodeEditor`). Line numbers,
 * folding, search, bracket matching, indentation, undo/redo, lint gutter, Korean IME via the
 * browser's native composition on contenteditable, `Tab` indents (announced in the UI), `Escape`
 * leaves the editor. The view is disposed on unmount.
 */
export const CodeEditorView = forwardRef<CodeEditorHandle, CodeEditorProps>(
  function CodeEditorView(
    {
      value,
      language,
      ariaLabel,
      onChange,
      onSelectionChange,
      onComposingChange,
      onEscape,
      diagnostics = [],
      completionSource,
      hoverSource,
      readOnly = false,
      initialHistoryState,
    },
    ref,
  ) {
    const host = useRef<HTMLDivElement>(null);
    const view = useRef<EditorView | null>(null);
    const callbacks = useRef({
      onChange,
      onSelectionChange,
      onComposingChange,
      onEscape,
    });
    callbacks.current = {
      onChange,
      onSelectionChange,
      onComposingChange,
      onEscape,
    };
    const languageCompartment = useRef(new Compartment());
    const readOnlyCompartment = useRef(new Compartment());
    const completionCompartment = useRef(new Compartment());
    const hoverCompartment = useRef(new Compartment());
    const labelCompartment = useRef(new Compartment());
    const extensionsRef = useRef<Extension[]>([]);
    // Latest props, so a restored state is reconfigured to what the editor shows now, not to
    // what it was created with.
    const latest = useRef({
      language,
      readOnly,
      completionSource,
      hoverSource,
      ariaLabel,
    });
    latest.current = {
      language,
      readOnly,
      completionSource,
      hoverSource,
      ariaLabel,
    };

    useEffect(() => {
      const parent = host.current;
      if (!parent) return;
      const extensions = [
        lineNumbers(),
        highlightActiveLineGutter(),
        highlightActiveLine(),
        foldGutter(),
        drawSelection(),
        indentOnInput(),
        bracketMatching(),
        highlightSelectionMatches(),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        history(),
        lintGutter(),
        linter(null),
        languageCompartment.current.of(language === "json" ? json() : yaml()),
        readOnlyCompartment.current.of(EditorState.readOnly.of(readOnly)),
        completionCompartment.current.of(
          completionSource
            ? autocompletion({ override: [toCmCompletion(completionSource)] })
            : autocompletion(),
        ),
        hoverCompartment.current.of(hoverSource ? toCmHover(hoverSource) : []),
        keymap.of([
          ...defaultKeymap,
          ...historyKeymap,
          ...searchKeymap,
          ...foldKeymap,
          ...lintKeymap,
          indentWithTab,
          {
            key: "Escape",
            run: (current) => {
              current.contentDOM.blur();
              callbacks.current.onEscape?.();
              return true;
            },
          },
        ]),
        labelCompartment.current.of(
          EditorView.contentAttributes.of({
            "aria-label": ariaLabel,
            "aria-multiline": "true",
          }),
        ),
        EditorView.domEventHandlers({
          compositionstart: () => {
            callbacks.current.onComposingChange?.(true);
          },
          compositionend: () => {
            callbacks.current.onComposingChange?.(false);
          },
        }),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            callbacks.current.onChange(
              update.state.doc.toString(),
              update.view.composing,
            );
          }
          // Report edit-owned cursor movement as mechanical editor state. The feature boundary
          // waits for that sourceVersion's parser map before assigning StrategySpec meaning.
          if (update.selectionSet) {
            const selection = update.state.selection.main;
            callbacks.current.onSelectionChange?.({
              from: selection.from,
              to: selection.to,
              documentChanged: update.docChanged,
            });
          }
        }),
      ];
      extensionsRef.current = extensions;
      const state =
        initialHistoryState !== undefined && initialHistoryState !== null
          ? EditorState.fromJSON(
              initialHistoryState,
              { extensions },
              HISTORY_FIELDS,
            )
          : EditorState.create({ doc: value, extensions });
      const created = new EditorView({ state, parent });
      view.current = created;
      return () => {
        created.destroy();
        view.current = null;
      };
      // The editor is created once; later prop changes are applied through compartments below.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
      view.current?.dispatch({
        effects: languageCompartment.current.reconfigure(
          language === "json" ? json() : yaml(),
        ),
      });
    }, [language]);

    useEffect(() => {
      view.current?.dispatch({
        effects: readOnlyCompartment.current.reconfigure(
          EditorState.readOnly.of(readOnly),
        ),
      });
    }, [readOnly]);

    useEffect(() => {
      view.current?.dispatch({
        effects: completionCompartment.current.reconfigure(
          completionSource
            ? autocompletion({ override: [toCmCompletion(completionSource)] })
            : autocompletion(),
        ),
      });
    }, [completionSource]);

    useEffect(() => {
      view.current?.dispatch({
        effects: hoverCompartment.current.reconfigure(
          hoverSource ? toCmHover(hoverSource) : [],
        ),
      });
    }, [hoverSource]);

    useEffect(() => {
      view.current?.dispatch({
        effects: labelCompartment.current.reconfigure(
          EditorView.contentAttributes.of({
            "aria-label": ariaLabel,
            "aria-multiline": "true",
          }),
        ),
      });
    }, [ariaLabel]);

    // Composition never outlives the editor: an unmount mid-IME would otherwise leave the
    // document state parked in "composing" with no event to release it.
    useEffect(
      () => () => {
        callbacks.current.onComposingChange?.(false);
      },
      [],
    );

    const lastDiagnostics = useRef<string>("");
    useEffect(() => {
      const current = view.current;
      if (!current) return;
      const key = JSON.stringify(diagnostics);
      if (key === lastDiagnostics.current) return;
      lastDiagnostics.current = key;
      current.dispatch(
        setDiagnostics(
          current.state,
          toCmDiagnostics(diagnostics, current.state.doc.length),
        ),
      );
    }, [diagnostics]);

    useImperativeHandle(
      ref,
      (): CodeEditorHandle => ({
        getText: () => view.current?.state.doc.toString() ?? "",
        setText: (text) => {
          const current = view.current;
          if (!current || current.state.doc.toString() === text) return;
          current.dispatch({
            changes: { from: 0, to: current.state.doc.length, insert: text },
          });
        },
        replaceRange: (from, to, text, selection) => {
          const current = view.current;
          if (!current) return;
          const length = current.state.doc.length;
          const safeFrom = Math.max(0, Math.min(from, length));
          const safeTo = Math.max(safeFrom, Math.min(to, length));
          const nextLength = length - (safeTo - safeFrom) + text.length;
          const nextSelection = selection
            ? EditorSelection.single(
                Math.max(0, Math.min(selection.from, nextLength)),
                Math.max(
                  0,
                  Math.min(selection.to ?? selection.from, nextLength),
                ),
              )
            : undefined;
          current.dispatch({
            changes: { from: safeFrom, to: safeTo, insert: text },
            selection: nextSelection,
            annotations: isolateHistory.of("full"),
          });
        },
        getSelection: () => {
          const range = view.current?.state.selection.main;
          return range
            ? { from: range.from, to: range.to }
            : { from: 0, to: 0 };
        },
        setSelection: (from, to = from) => {
          view.current?.dispatch({
            selection: EditorSelection.single(from, to),
          });
        },
        offsetToPosition: (offset) => {
          const current = view.current;
          if (!current) return { line: 0, column: 0 };
          const clamped = Math.max(
            0,
            Math.min(offset, current.state.doc.length),
          );
          const line = current.state.doc.lineAt(clamped);
          return { line: line.number - 1, column: clamped - line.from };
        },
        positionToOffset: ({ line, column }) => {
          const current = view.current;
          if (!current) return 0;
          const lines = current.state.doc.lines;
          const target = current.state.doc.line(
            Math.max(1, Math.min(line + 1, lines)),
          );
          return Math.min(target.from + Math.max(0, column), target.to);
        },
        scrollTo: (offset) => {
          view.current?.dispatch({
            effects: EditorView.scrollIntoView(offset, { y: "center" }),
          });
        },
        focus: () => view.current?.focus(),
        getHistoryState: () =>
          view.current?.state.toJSON(HISTORY_FIELDS) ?? null,
        restoreHistoryState: (state) => {
          const current = view.current;
          if (!current || state === null || state === undefined) return;
          current.setState(
            EditorState.fromJSON(
              state,
              { extensions: extensionsRef.current },
              HISTORY_FIELDS,
            ),
          );
          // Serialised extensions carry mount-time compartment values; restore current props.
          const props = latest.current;
          current.dispatch({
            effects: [
              languageCompartment.current.reconfigure(
                props.language === "json" ? json() : yaml(),
              ),
              readOnlyCompartment.current.reconfigure(
                EditorState.readOnly.of(props.readOnly),
              ),
              completionCompartment.current.reconfigure(
                props.completionSource
                  ? autocompletion({
                      override: [toCmCompletion(props.completionSource)],
                    })
                  : autocompletion(),
              ),
              hoverCompartment.current.reconfigure(
                props.hoverSource ? toCmHover(props.hoverSource) : [],
              ),
              labelCompartment.current.reconfigure(
                EditorView.contentAttributes.of({
                  "aria-label": props.ariaLabel,
                  "aria-multiline": "true",
                }),
              ),
            ],
          });
        },
      }),
      [],
    );

    return <div ref={host} className="code-editor" data-language={language} />;
  },
);
