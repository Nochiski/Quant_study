import {
  type ReactNode,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type { StrategyOutlineSymbol } from "../../../features/edit-strategy";
import { t } from "../../../shared/config";
import { useMediaQuery } from "../../../shared/lib/media";
import { useThemePreference } from "../../../shared/lib/theme";
import {
  Badge,
  Button,
  CommandPalette,
  SplitHandle,
  Tabs,
  type CommandPaletteItem,
  panelId,
  tabId,
} from "../../../shared/ui";
import {
  DEFAULT_LAYOUT,
  PANEL_BOUNDS,
  usePanelLayout,
} from "../model/use-panel-layout";
import "./strategy-ide.css";

export type SourceView = "yaml" | "json" | "form" | "graph" | "diff";

export type StrategyIdeProps = {
  title: string;
  /** Revision label shown next to the breadcrumb and the title, e.g. "v12" or "초안". */
  versionLabel: string;
  /** Header badges next to the title (draft/revision markers). */
  badges?: ReactNode;
  /** Meta line under the title: author, created, updated. Missing values render as "—". */
  meta?: {
    author?: string;
    createdAt?: string;
    updatedAt?: string;
    /** Backend-reported identity of the current text (P3-05); never computed client-side. */
    schemaVersion?: string | null;
    sourceHash?: string | null;
    specHash?: string | null;
  };
  /** Save status text in the top bar, e.g. "방금 저장됨". */
  saveStatus?: string;
  saveTone?: "ok" | "warn" | "error";
  onRunBacktest?: () => void;
  runDisabled?: boolean;
  onValidate?: () => void;
  validateDisabled?: boolean;
  onSave?: () => void;
  saveDisabled?: boolean;
  /**
   * 되돌리기·다시 실행(WORKFLOW P1-02, spec D9). 편집기가 hidden인 탭에서도 동작해야 하므로 IDE가 전역
   * 단축키로 받아 넘긴다. 할 일이 없을 때 아무 일도 하지 않는 책임은 편집기 이력에 있다 — 여기서 깊이로
   * 게이트를 걸면 깊이가 한 프레임 늦은 순간의 입력이 조용히 버려진다(Phase 5 backlog 20과 같은 경합).
   */
  onUndo?: () => void;
  onRedo?: () => void;
  symbols?: readonly StrategyOutlineSymbol[];
  onSelectSymbol?: (pointer: string) => void;
  /** The source editor slot (P3). */
  editor: ReactNode;
  /** Source tab whose editor must stay mounted while read-only projections are selected. */
  sourceView?: "yaml" | "json";
  /** Stable read-only tab content keyed by representation. */
  projections?: Partial<Record<SourceView, ReactNode>>;
  /** Document-level recovery or warning UI that must remain visible across every view. */
  notice?: ReactNode;
  /** Strategy document outline projection (P4-01). */
  outline?: ReactNode;
  /** P4-10 catalog UI; the P4-05 feature model owns schema projection and insertion. */
  snippets?: ReactNode;
  /** Editor toolbar actions (format / validate) rendered in the editor header. */
  editorActions?: ReactNode;
  /**
   * 되돌리기·다시 실행 버튼. 탭 목록 줄 — 탭 패널 밖이라 편집기가 hidden인 Graph·Form 탭에서도 닿는다
   * (WORKFLOW P1-02). 편집기 헤더의 액션 줄은 이미 꽉 차 있어 상태 배지와 같은 줄에 둔다.
   */
  documentHistory?: ReactNode;
  /**
   * 문서 상태 배지(검증 통과·구조 오류·STALE). 탭 목록 줄의 오른쪽 — 탭 패널 밖이라 다섯 탭 모두에서
   * 보인다(WORKFLOW P1-01).
   */
  documentStatus?: ReactNode;
  /** 문제 목록. 탭 패널 밖(편집 패널 아래)이라 다섯 탭 모두에서 보인다(WORKFLOW P1-01). */
  problems?: ReactNode;
  view?: SourceView;
  onViewChange?: (view: SourceView) => void;
  /** Views the caller can render; the rest are shown disabled. */
  availableViews?: readonly SourceView[];
  /** Contract Inspector slot (P4); placeholder until then. */
  inspector?: ReactNode;
  /** Intermediate Debugger slot (P5); placeholder until then. */
  debugger?: ReactNode;
};

const NARROW_QUERY = "(max-width: 1279px)";

/** 브라우저·편집기가 스스로 되돌리기를 갖는 입력 타입. 나머지(select·button)는 갖지 않는다. */
const NATIVE_UNDO_INPUT_TYPES = new Set([
  "text",
  "search",
  "url",
  "tel",
  "email",
  "password",
  "number",
  "date",
  "month",
  "week",
  "time",
  "datetime-local",
]);

/**
 * 이 입력이 자기 자신의 되돌리기를 가진 곳에서 왔는가(WORKFLOW P1-02). 텍스트 입력·textarea·
 * contenteditable(CodeMirror의 편집 영역이 여기 해당한다)에 포커스가 있으면 Ctrl+Z를 가로채지 않는다 —
 * 가로채면 한 번의 입력이 그 자리의 되돌리기와 문서 되돌리기를 둘 다 실행한다.
 */
const hasNativeUndo = (target: EventTarget | null): boolean => {
  if (!(target instanceof HTMLElement)) return false;
  // `isContentEditable`은 상속까지 반영하지만 jsdom에는 없다 — 속성도 함께 본다.
  if (target.isContentEditable) return true;
  if (target.closest('[contenteditable=""], [contenteditable="true"]') !== null)
    return true;
  if (target instanceof HTMLTextAreaElement) return true;
  return (
    target instanceof HTMLInputElement &&
    NATIVE_UNDO_INPUT_TYPES.has(target.type)
  );
};

const VIEWS: readonly SourceView[] = ["yaml", "json", "form", "graph", "diff"];

/**
 * Strategy IDE frame laid out like the concept: top bar (breadcrumb, save status, run), title
 * with meta line, left Outline + Snippets, centre editor with format/validate actions and the
 * YAML/JSON/Form/Graph/Diff tabs, right Contract Inspector, bottom Intermediate Results. All
 * panels resize and collapse; collapsed panels stay in the DOM (`hidden`) so every toggle's
 * `aria-controls` resolves. Below 1280px the inspector and debugger become non-modal drawers,
 * closed by default, toggled from the top bar and dismissed with Escape. There is deliberately
 * no top Data→…→Execution stepper: the outline is the only navigation.
 */
export const StrategyIde = ({
  title,
  versionLabel,
  badges,
  meta,
  saveStatus,
  saveTone = "ok",
  onRunBacktest,
  runDisabled = false,
  onValidate,
  validateDisabled = true,
  onSave,
  saveDisabled = true,
  onUndo,
  onRedo,
  symbols = [],
  onSelectSymbol,
  editor,
  sourceView,
  projections,
  notice,
  outline,
  snippets,
  editorActions,
  documentHistory,
  documentStatus,
  problems,
  view = "yaml",
  onViewChange,
  availableViews = ["yaml"],
  inspector,
  debugger: debuggerPanel,
}: StrategyIdeProps) => {
  const narrow = useMediaQuery(NARROW_QUERY);
  const { layout, resize, toggle, close } = usePanelLayout(
    narrow
      ? { ...DEFAULT_LAYOUT, inspectorOpen: false, debuggerOpen: false }
      : DEFAULT_LAYOUT,
  );
  const outlineId = useId();
  const inspectorId = useId();
  const debuggerId = useId();
  const viewTabsId = useId();
  const ids = {
    outline: outlineId,
    inspector: inspectorId,
    debugger: debuggerId,
    views: viewTabsId,
  };
  const outlineRestore = useRef<HTMLButtonElement>(null);
  const inspectorRestore = useRef<HTMLButtonElement>(null);
  const debuggerRestore = useRef<HTMLButtonElement>(null);
  const outlineCollapse = useRef<HTMLButtonElement>(null);
  const inspectorCollapse = useRef<HTMLButtonElement>(null);
  const debuggerCollapse = useRef<HTMLButtonElement>(null);
  const theme = useThemePreference();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const commands = useMemo<CommandPaletteItem[]>(() => {
    const actionCommands: CommandPaletteItem[] = [
      {
        id: "action.validate",
        group: t("command.group.action"),
        label: t("toolbar.validate"),
        shortcut: "Ctrl/⌘ Enter",
        disabled: validateDisabled || !onValidate,
        execute: () => onValidate?.(),
      },
      {
        id: "action.save",
        group: t("command.group.action"),
        label: t("toolbar.saveRevision"),
        shortcut: "Ctrl/⌘ S",
        disabled: saveDisabled || !onSave,
        execute: () => onSave?.(),
      },
      {
        id: "action.undo",
        group: t("command.group.action"),
        label: t("ide.undo"),
        shortcut: "Ctrl/⌘ Z",
        disabled: !onUndo,
        execute: () => onUndo?.(),
      },
      {
        id: "action.redo",
        group: t("command.group.action"),
        label: t("ide.redo"),
        shortcut: "Ctrl/⌘ Shift Z",
        disabled: !onRedo,
        execute: () => onRedo?.(),
      },
      {
        id: "action.backtest",
        group: t("command.group.action"),
        label: t("ide.runBacktest"),
        shortcut: "Ctrl/⌘ Shift Enter",
        disabled: runDisabled || !onRunBacktest,
        execute: () => onRunBacktest?.(),
      },
    ];
    const viewCommands: CommandPaletteItem[] = VIEWS.map((id, index) => ({
      id: `view.${id}`,
      group: t("command.group.view"),
      label: `${t("command.openView")} ${id.toUpperCase()}`,
      description: id === view ? t("command.current") : undefined,
      shortcut: `Alt ${index + 1}`,
      disabled: !availableViews.includes(id) || !onViewChange,
      focusAfterExecute: () => document.getElementById(tabId(viewTabsId, id)),
      execute: () => onViewChange?.(id),
    }));
    const panelCommands: CommandPaletteItem[] = [
      {
        id: "panel.outline",
        group: t("command.group.panel"),
        label: `${layout.outlineOpen ? t("command.hide") : t("command.show")} ${t("ide.outline")}`,
        focusAfterExecute: () =>
          layout.outlineOpen ? outlineRestore.current : outlineCollapse.current,
        execute: () => toggle("outlineOpen"),
      },
      {
        id: "panel.inspector",
        group: t("command.group.panel"),
        label: `${layout.inspectorOpen ? t("command.hide") : t("command.show")} ${t("ide.inspector")}`,
        focusAfterExecute: () =>
          layout.inspectorOpen
            ? inspectorRestore.current
            : inspectorCollapse.current,
        execute: () => toggle("inspectorOpen"),
      },
      {
        id: "panel.debugger",
        group: t("command.group.panel"),
        label: `${layout.debuggerOpen ? t("command.hide") : t("command.show")} ${t("ide.debugger")}`,
        focusAfterExecute: () =>
          layout.debuggerOpen
            ? debuggerRestore.current
            : debuggerCollapse.current,
        execute: () => toggle("debuggerOpen"),
      },
    ];
    const themeCommands: CommandPaletteItem[] = (
      ["system", "light", "dark"] as const
    ).map((preference) => ({
      id: `theme.${preference}`,
      group: t("command.group.theme"),
      label: t(`command.theme.${preference}`),
      description:
        theme.preference === preference ? t("command.current") : undefined,
      disabled: theme.preference === preference,
      execute: () => theme.setPreference(preference),
    }));
    const symbolCommands: CommandPaletteItem[] = symbols.map((symbol) => ({
      id: `symbol.${symbol.id}`,
      group: t("command.group.symbol"),
      label: symbol.label,
      description: symbol.description,
      keywords: symbol.keywords,
      disabled: !onSelectSymbol,
      execute: () => onSelectSymbol?.(symbol.pointer),
    }));
    return [
      ...actionCommands,
      ...viewCommands,
      ...panelCommands,
      ...themeCommands,
      ...symbolCommands,
    ];
  }, [
    availableViews,
    layout,
    onRedo,
    onRunBacktest,
    onSave,
    onSelectSymbol,
    onUndo,
    onValidate,
    onViewChange,
    runDisabled,
    saveDisabled,
    symbols,
    theme,
    toggle,
    validateDisabled,
    view,
    viewTabsId,
  ]);

  useEffect(() => {
    if (narrow) close(["inspectorOpen", "debuggerOpen"]);
  }, [narrow, close]);

  // window 리스너는 layout effect로 설치한다(`.claude/rules/frontend-react-effects.md`, backlog 20·21).
  useLayoutEffect(() => {
    if (!narrow) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close(["inspectorOpen", "debuggerOpen"]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [narrow, close]);

  // layout effect인 이유: 이 리스너는 화면의 버튼과 같은 게이트(`saveDisabled`·`runDisabled`·`validateDisabled`)를
  // 닫힌 값으로 읽는다. passive effect(`useEffect`)면 버튼이 켜진 commit과 새 리스너 설치 사이에 틈이 생겨, 그
  // 사이에 온 Ctrl+S / Ctrl+Shift+Enter는 옛 닫힌 값(비활성)으로 조용히 버려진다 — route 테스트가 게이트 케이스에서
  // 드물게 실패하던 원인(Phase 5 backlog 20). layout effect는 commit 안에서 동기로 갈아 끼운다
  // (규칙: `.claude/rules/frontend-react-effects.md`).
  useLayoutEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.isComposing || event.keyCode === 229) return;
      const modifier = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();
      if (modifier && key === "k") {
        event.preventDefault();
        if (event.repeat) return;
        setPaletteOpen((open) => !open);
        return;
      }
      if (paletteOpen) return;
      if (modifier && key === "z") {
        // 편집기가 hidden인 탭(Graph·Form)에서는 CodeMirror 키맵이 포커스를 못 받아 Ctrl+Z가 사라진다.
        // 포커스가 스스로 되돌리기를 가진 곳에 있으면 그쪽에 양보한다(중복 undo 금지).
        if (hasNativeUndo(event.target)) return;
        const run = event.shiftKey ? onRedo : onUndo;
        if (run === undefined) return;
        event.preventDefault();
        // 다른 단축키와 달리 auto-repeat를 막지 않는다 — 눌러 두고 여러 단계를 되돌리는 것이 편집기의
        // 통상 동작이고, 할 일이 없어지면 편집기 이력이 스스로 멈춘다.
        run();
      } else if (modifier && key === "s") {
        event.preventDefault();
        if (!event.repeat && !saveDisabled) onSave?.();
      } else if (modifier && event.key === "Enter") {
        event.preventDefault();
        if (event.repeat) return;
        if (event.shiftKey) {
          if (!runDisabled) onRunBacktest?.();
        } else if (!validateDisabled) onValidate?.();
      } else if (event.altKey && !modifier && /^[1-5]$/.test(event.key)) {
        const next = VIEWS[Number(event.key) - 1];
        if (
          event.repeat ||
          !next ||
          !availableViews.includes(next) ||
          !onViewChange
        )
          return;
        event.preventDefault();
        onViewChange(next);
        queueMicrotask(() =>
          document.getElementById(tabId(viewTabsId, next))?.focus(),
        );
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    availableViews,
    onRedo,
    onRunBacktest,
    onSave,
    onUndo,
    onValidate,
    onViewChange,
    paletteOpen,
    runDisabled,
    saveDisabled,
    validateDisabled,
    viewTabsId,
  ]);

  const inspectorNode = (
    <aside
      id={ids.inspector}
      className="ide__inspector"
      aria-label={t("ide.inspector")}
      hidden={!layout.inspectorOpen}
      style={narrow ? undefined : { width: layout.inspectorWidth }}
    >
      <header className="ide__panel-header">
        <h2>{t("ide.inspector")}</h2>
        <Button
          ref={inspectorCollapse}
          size="small"
          tone="ghost"
          onClick={() => {
            toggle("inspectorOpen");
            queueMicrotask(() => inspectorRestore.current?.focus());
          }}
        >
          {t("ide.collapseInspector")}
        </Button>
      </header>
      {inspector ?? (
        <p className="ide__outline-placeholder">{t("ide.placeholder")}</p>
      )}
    </aside>
  );

  const debuggerNode = (
    <section
      id={ids.debugger}
      className="ide__debugger"
      aria-label={t("ide.debugger")}
      hidden={!layout.debuggerOpen}
      style={narrow ? undefined : { height: layout.debuggerHeight }}
    >
      <header className="ide__panel-header">
        <h2>{t("ide.debugger")}</h2>
        <Button
          ref={debuggerCollapse}
          size="small"
          tone="ghost"
          onClick={() => {
            toggle("debuggerOpen");
            queueMicrotask(() => debuggerRestore.current?.focus());
          }}
        >
          {t("ide.collapseDebugger")}
        </Button>
      </header>
      {debuggerPanel ?? <DebuggerPlaceholder />}
    </section>
  );

  return (
    <div className="ide">
      <header className="ide__topbar">
        <nav className="ide__breadcrumb" aria-label={t("ide.breadcrumb")}>
          <span className="ide__crumb">{t("nav.strategies")}</span>
          <span className="ide__crumb-sep" aria-hidden="true">
            /
          </span>
          <span className="ide__crumb ide__crumb--current" aria-current="page">
            {title} {versionLabel}
          </span>
          <Badge tone="accent">{t("nav.research")}</Badge>
        </nav>
        <div className="ide__topbar-status" role="status">
          {saveStatus ? (
            <>
              <span aria-hidden="true">
                {saveTone === "error" ? "✕" : saveTone === "warn" ? "!" : "✓"}
              </span>{" "}
              {saveStatus}
            </>
          ) : null}
        </div>
        <div className="ide__topbar-actions">
          <Button
            size="small"
            tone="ghost"
            onClick={() => setPaletteOpen(true)}
            aria-haspopup="dialog"
            aria-keyshortcuts="Control+K Meta+K"
          >
            {t("command.open")} <kbd>Ctrl/⌘ K</kbd>
          </Button>
          {!layout.outlineOpen ? (
            <Button
              ref={outlineRestore}
              size="small"
              onClick={() => {
                toggle("outlineOpen");
                queueMicrotask(() => outlineCollapse.current?.focus());
              }}
              aria-controls={ids.outline}
              aria-expanded={false}
            >
              {t("ide.outline")}
            </Button>
          ) : null}
          {narrow || !layout.inspectorOpen ? (
            <Button
              ref={inspectorRestore}
              size="small"
              onClick={() => {
                toggle("inspectorOpen");
                queueMicrotask(() => inspectorCollapse.current?.focus());
              }}
              aria-controls={ids.inspector}
              aria-expanded={layout.inspectorOpen}
            >
              {t("ide.inspector")}
            </Button>
          ) : null}
          {narrow || !layout.debuggerOpen ? (
            <Button
              ref={debuggerRestore}
              size="small"
              onClick={() => {
                toggle("debuggerOpen");
                queueMicrotask(() => debuggerCollapse.current?.focus());
              }}
              aria-controls={ids.debugger}
              aria-expanded={layout.debuggerOpen}
            >
              {t("ide.debugger")}
            </Button>
          ) : null}
          <Button
            tone="primary"
            onClick={onRunBacktest}
            disabled={runDisabled || !onRunBacktest}
            aria-keyshortcuts="Control+Shift+Enter Meta+Shift+Enter"
          >
            <span aria-hidden="true">▷</span> {t("ide.runBacktest")}
          </Button>
        </div>
      </header>

      <header className="ide__title">
        <div className="ide__title-row">
          <h1>{title}</h1>
          {badges}
        </div>
        <dl className="ide__meta">
          <div>
            <dt>{t("ide.meta.version")}</dt>
            <dd>
              <Badge tone="neutral">{versionLabel}</Badge>
            </dd>
          </div>
          <div>
            <dt>{t("ide.meta.author")}</dt>
            <dd>{meta?.author ?? "—"}</dd>
          </div>
          <div>
            <dt>{t("ide.meta.createdAt")}</dt>
            <dd>{meta?.createdAt ?? "—"}</dd>
          </div>
          <div>
            <dt>{t("ide.meta.updatedAt")}</dt>
            <dd>{meta?.updatedAt ?? "—"}</dd>
          </div>
          {meta?.schemaVersion !== undefined ? (
            <div>
              <dt>{t("ide.meta.schemaVersion")}</dt>
              <dd>{meta.schemaVersion ?? "—"}</dd>
            </div>
          ) : null}
          {meta?.sourceHash !== undefined ? (
            <div>
              <dt>{t("ide.meta.sourceHash")}</dt>
              <dd>
                <code title={meta.sourceHash ?? undefined}>
                  {meta.sourceHash ? `${meta.sourceHash.slice(0, 12)}…` : "—"}
                </code>
              </dd>
            </div>
          ) : null}
          {meta?.specHash !== undefined ? (
            <div>
              <dt>{t("ide.meta.specHash")}</dt>
              <dd>
                <code title={meta.specHash ?? undefined}>
                  {meta.specHash ? `${meta.specHash.slice(0, 12)}…` : "—"}
                </code>
              </dd>
            </div>
          ) : null}
        </dl>
      </header>

      {notice ? <div className="ide__notice">{notice}</div> : null}

      <div className="ide__body">
        <div
          className="ide__left"
          hidden={!layout.outlineOpen}
          style={{ width: layout.outlineWidth }}
        >
          <nav
            id={ids.outline}
            className="ide__outline"
            aria-label={t("ide.outline")}
          >
            <header className="ide__panel-header">
              <h2>{t("ide.outline")}</h2>
              <Button
                ref={outlineCollapse}
                size="small"
                tone="ghost"
                onClick={() => {
                  toggle("outlineOpen");
                  queueMicrotask(() => outlineRestore.current?.focus());
                }}
              >
                {t("ide.collapseOutline")}
              </Button>
            </header>
            {outline ?? (
              <p className="ide__outline-placeholder">{t("ide.placeholder")}</p>
            )}
          </nav>
          <section className="ide__snippets" aria-label={t("ide.snippets")}>
            <h2 className="ide__snippets-title">{t("ide.snippets")}</h2>
            {snippets ?? (
              <ul className="ide__snippet-list">
                {(["field", "transform", "risk"] as const).map((snippet) => (
                  <li key={snippet}>
                    <button type="button" className="ide__snippet" disabled>
                      <span aria-hidden="true">
                        {snippet === "transform" ? "ƒx" : "▢"}
                      </span>
                      <span>{t(`ide.snippet.${snippet}`)}</span>
                      <span className="ide__snippet-plus" aria-hidden="true">
                        +
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
        {layout.outlineOpen ? (
          <SplitHandle
            orientation="vertical"
            label={t("ide.resizeOutline")}
            value={layout.outlineWidth}
            min={PANEL_BOUNDS.outlineWidth.min}
            max={PANEL_BOUNDS.outlineWidth.max}
            onChange={(value) => resize("outlineWidth", value)}
            controls={ids.outline}
          />
        ) : null}

        <div className="ide__centre">
          <section className="ide__editor" aria-label={t("ide.editor")}>
            <header className="ide__editor-header">
              <div className="ide__editor-heading">
                <strong>StrategySpec</strong>
                <span className="ide__editor-sub">
                  {view.toUpperCase()} · {t("ide.editor.verbose")}
                </span>
              </div>
              <div className="ide__editor-actions">{editorActions}</div>
            </header>
            <div className="ide__editor-tabs">
              <Tabs
                label={t("ui.tabs.view")}
                idBase={ids.views}
                items={VIEWS.map((id) => ({
                  id,
                  label:
                    id === "yaml" || id === "json"
                      ? id.toUpperCase()
                      : capitalize(id),
                  disabled: !availableViews.includes(id),
                }))}
                value={view}
                onChange={(next) => onViewChange?.(next)}
              />
              {documentHistory ? (
                <div className="ide__editor-history">{documentHistory}</div>
              ) : null}
              {documentStatus ? (
                <div className="ide__editor-status">{documentStatus}</div>
              ) : null}
            </div>
            {VIEWS.map((id) => (
              <div
                key={id}
                id={panelId(ids.views, id)}
                role="tabpanel"
                aria-labelledby={tabId(ids.views, id)}
                className="ide__editor-panel"
                hidden={id !== view}
              >
                {sourceView === undefined
                  ? id === view
                    ? editor
                    : null
                  : id === sourceView
                    ? editor
                    : projections?.[id]}
              </div>
            ))}
            {problems ? (
              <div className="ide__problems">{problems}</div>
            ) : null}
          </section>
          {!narrow && layout.debuggerOpen ? (
            <SplitHandle
              orientation="horizontal"
              label={t("ide.resizeDebugger")}
              value={layout.debuggerHeight}
              min={PANEL_BOUNDS.debuggerHeight.min}
              max={PANEL_BOUNDS.debuggerHeight.max}
              invert
              onChange={(value) => resize("debuggerHeight", value)}
              controls={ids.debugger}
            />
          ) : null}
          {narrow ? null : debuggerNode}
        </div>

        {!narrow && layout.inspectorOpen ? (
          <SplitHandle
            orientation="vertical"
            label={t("ide.resizeInspector")}
            value={layout.inspectorWidth}
            min={PANEL_BOUNDS.inspectorWidth.min}
            max={PANEL_BOUNDS.inspectorWidth.max}
            invert
            onChange={(value) => resize("inspectorWidth", value)}
            controls={ids.inspector}
          />
        ) : null}
        {narrow ? null : inspectorNode}
      </div>

      {narrow ? (
        <>
          <div className="ide__drawer" hidden={!layout.inspectorOpen}>
            {inspectorNode}
          </div>
          <div
            className="ide__drawer ide__drawer--bottom"
            hidden={!layout.debuggerOpen}
          >
            {debuggerNode}
          </div>
        </>
      ) : null}
      <CommandPalette
        open={paletteOpen}
        label={t("command.palette")}
        searchLabel={t("command.search")}
        searchPlaceholder={t("command.searchPlaceholder")}
        emptyLabel={t("command.empty")}
        closeLabel={t("command.close")}
        commands={commands}
        onClose={() => setPaletteOpen(false)}
      />
    </div>
  );
};

const capitalize = (value: string) =>
  value.charAt(0).toUpperCase() + value.slice(1);

const RESULT_TABS = [
  { id: "preview", label: t("ide.debugger.tab.preview") },
  { id: "exposure", label: t("ide.debugger.tab.exposure") },
  { id: "orders", label: t("ide.debugger.tab.orders") },
  { id: "exclusions", label: t("ide.debugger.tab.exclusions") },
  { id: "plan", label: t("ide.debugger.tab.plan") },
] as const;

type ResultTab = (typeof RESULT_TABS)[number]["id"];

const SAMPLE_ROWS = [
  ["삼성전자", "7.4%", "5.0%", "-2.4%", "Clipped", "warn"],
  ["SK하이닉스", "4.2%", "4.2%", "0.0%", "OK", "ok"],
  ["LG에너지솔루션", "3.1%", "3.1%", "0.0%", "OK", "ok"],
  ["현대차", "2.8%", "2.8%", "0.0%", "OK", "ok"],
  ["POSCO홀딩스", "2.6%", "2.6%", "0.0%", "OK", "ok"],
] as const;

/** Shape of the concept's intermediate-results panel; real values arrive with P5. */
const DebuggerPlaceholder = () => {
  const idBase = useId();
  const [tab, setTab] = useState<ResultTab>("preview");
  return (
    <div className="ide__results">
      <div className="ide__results-summary">
        <button type="button" className="ide__selector" disabled>
          <span aria-hidden="true">▤</span> <code>/risk/max_name_weight</code> ·
          2026-08-31
          <span aria-hidden="true"> ▾</span>
        </button>
        <p className="ide__before-after">
          <span>
            {t("ide.debugger.before")}{" "}
            <strong className="ide__value--warn">7.4%</strong>
          </span>
          <span aria-hidden="true">⟶</span>
          <span>
            {t("ide.debugger.after")}{" "}
            <strong className="ide__value--ok">5.0%</strong>
          </span>
        </p>
        <div className="ide__card">
          <span>{t("ide.placeholder")}</span>
        </div>
        <p className="text-small ide__sample-note">{t("ide.placeholder")}</p>
      </div>
      <div className="ide__results-table">
        <Tabs
          label={t("ide.debugger")}
          items={RESULT_TABS}
          value={tab}
          onChange={setTab}
          idBase={idBase}
        />
        {RESULT_TABS.map((item) => (
          <div
            key={item.id}
            id={panelId(idBase, item.id)}
            role="tabpanel"
            aria-labelledby={tabId(idBase, item.id)}
            hidden={item.id !== tab}
          >
            {item.id === "preview" ? (
              <table className="ide__table">
                <caption className="ide__sample-note">
                  {t("ide.placeholder")}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">{t("ide.debugger.security")}</th>
                    <th scope="col">{t("ide.debugger.computedWeight")}</th>
                    <th scope="col">{t("ide.debugger.afterCap")}</th>
                    <th scope="col">{t("ide.debugger.difference")}</th>
                    <th scope="col">{t("ide.debugger.status")}</th>
                  </tr>
                </thead>
                <tbody>
                  {SAMPLE_ROWS.map(
                    ([name, computed, after, diff, status, tone]) => (
                      <tr
                        key={name}
                        className={
                          tone === "warn" ? "ide__row--warn" : undefined
                        }
                      >
                        <td>{name}</td>
                        <td
                          className={
                            tone === "warn" ? "ide__value--warn" : undefined
                          }
                        >
                          {computed}
                        </td>
                        <td
                          className={
                            tone === "warn" ? "ide__value--warn" : undefined
                          }
                        >
                          {after}
                        </td>
                        <td
                          className={
                            tone === "warn" ? "ide__value--warn" : undefined
                          }
                        >
                          {diff}
                        </td>
                        <td>
                          <Badge tone={tone === "warn" ? "warn" : "ok"}>
                            {status}
                          </Badge>
                        </td>
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
            ) : (
              <p className="text-small">{t("ide.placeholder")}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};
