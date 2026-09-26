import {
  type ReactNode,
  useCallback,
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
  /**
   * 우측 AI 어시스턴트 사이드바 슬롯(B-04). 넘기지 않으면 패널도 토글도 만들지 않는다 — 어시스턴트를
   * 붙이지 않은 화면의 배치는 그대로다. 내용은 페이지가 주입한다(widget은 채팅을 알지 않는다).
   *
   * 함수를 넘기면 패널을 접는 손잡이를 받는다. 사이드바의 "닫기"가 진행 중 턴 취소를 확인한 뒤 패널을
   * 접는 경로다(spec D7). 패널 헤더의 접기 버튼은 대화를 끝내지 않는 패널 조작이라 확인을 거치지 않는다.
   */
  assistant?: ReactNode | ((controls: AssistantSlotControls) => ReactNode);
};

/** 슬롯 함수가 받는 패널 손잡이. */
export type AssistantSlotControls = { close: () => void };

const NARROW_QUERY = "(max-width: 1279px)";
/** 좌우 패널이 다 펼쳐졌을 때 가운데 편집기에 남겨 두는 최소 폭. 이 아래로 내려가면 오버레이로 돌린다. */
const EDITOR_MIN_WIDTH = 480;
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
  symbols = [],
  onSelectSymbol,
  editor,
  sourceView,
  projections,
  notice,
  outline,
  snippets,
  editorActions,
  documentStatus,
  problems,
  view = "yaml",
  onViewChange,
  availableViews = ["yaml"],
  inspector,
  debugger: debuggerPanel,
  assistant,
}: StrategyIdeProps) => {
  const narrow = useMediaQuery(NARROW_QUERY);
  const { layout, resize, toggle, close } = usePanelLayout(
    narrow
      ? { ...DEFAULT_LAYOUT, inspectorOpen: false, debuggerOpen: false }
      : DEFAULT_LAYOUT,
  );
  // 계약과 AI 사이드바를 나란히 두면 편집기가 최소 폭 아래로 내려가는 화면인가.
  //
  // 사이드바 폭은 **기본값 상수**로 재고 지금 폭을 쓰지 않는다. 지금 폭을 쓰면 폭 조절 드래그가 질의를
  // 바꿔, 임계를 넘는 순간 패널이 오버레이로 바뀌며 핸들이 사라지고(포인터 캡처가 끊긴다) 저장된 폭
  // 때문에 다음 방문에도 오버레이로 굳는다(B-04 리뷰 P1-3). 오버레이 전환은 "패널을 열었다"로만
  // 일어나야 한다. 접힌 패널은 자리를 차지하지 않으므로 더하지 않는다.
  const squeezed = useMediaQuery(
    `(max-width: ${
      (layout.outlineOpen ? layout.outlineWidth : 0) +
      (layout.inspectorOpen ? layout.inspectorWidth : 0) +
      DEFAULT_LAYOUT.assistantWidth +
      EDITOR_MIN_WIDTH -
      1
    }px)`,
  );
  const outlineId = useId();
  const inspectorId = useId();
  const debuggerId = useId();
  const assistantId = useId();
  const viewTabsId = useId();
  const ids = {
    outline: outlineId,
    inspector: inspectorId,
    debugger: debuggerId,
    assistant: assistantId,
    views: viewTabsId,
  };
  const outlineRestore = useRef<HTMLButtonElement>(null);
  const inspectorRestore = useRef<HTMLButtonElement>(null);
  const debuggerRestore = useRef<HTMLButtonElement>(null);
  const outlineCollapse = useRef<HTMLButtonElement>(null);
  const inspectorCollapse = useRef<HTMLButtonElement>(null);
  const debuggerCollapse = useRef<HTMLButtonElement>(null);
  const assistantRestore = useRef<HTMLButtonElement>(null);
  const assistantCollapse = useRef<HTMLButtonElement>(null);
  const hasAssistant = assistant !== undefined;
  /**
   * 좁은 화면에서 둘 다 펼쳐져 있으면 **나중에 연 쪽**을 오버레이로 돌린다. 먼저 보고 있던 패널을
   * 빼앗지 않으면서 편집기 폭을 지키는 규칙이다.
   */
  const overlayRight =
    !narrow && squeezed && hasAssistant && layout.inspectorOpen && layout.assistantOpen
      ? (layout.lastOpenedRight ?? "assistantOpen")
      : null;
  const inspectorFloating = narrow || overlayRight === "inspectorOpen";
  const assistantFloating = narrow || overlayRight === "assistantOpen";
  // 기본이 접힘인데 내용을 미리 마운트하면 화면을 열 때마다 사이드바의 질의가 나간다. 한 번 펼친
  // 뒤에는 접어도 유지한다 — 진행 중 턴의 스트림이 접기로 끊기면 안 된다(B-04 리뷰 P3).
  const [assistantMounted, setAssistantMounted] = useState(layout.assistantOpen);
  /**
   * 오른쪽 패널 토글. 좁은 화면에서는 계약·AI 서랍이 같은 자리(`position: fixed; right: 0`)에 뜨므로
   * 한 번에 하나만 연다 — 겹치면 뒤에 깔린 패널이 보이지 않은 채 탭 순서와 접근성 트리에 남는다
   * (B-04 리뷰 P1-2).
   */
  const toggleRight = useCallback(
    (panel: "inspectorOpen" | "assistantOpen"): void => {
      const other =
        panel === "inspectorOpen" ? "assistantOpen" : "inspectorOpen";
      const opening = !layout[panel];
      if (opening && narrow && layout[other]) close([other]);
      if (opening && panel === "assistantOpen") setAssistantMounted(true);
      toggle(panel);
    },
    [close, layout, narrow, toggle],
  );
  // 슬롯 함수는 렌더 중에 불리므로 이 손잡이는 ref를 닫지 않는다(`react-hooks/refs`). 접은 뒤 돌아갈
  // 자리는 상단 바 토글이고, 그 자리는 id로 찾는다.
  const assistantToggleId = `${assistantId}-toggle`;
  /**
   * 사이드바를 펼친 뒤 포커스가 갈 자리. 슬롯이 접기 버튼을 그리면 그 버튼, 아니면 패널 자신이다
   * (`tabIndex={-1}`) — 상단 토글은 펼치는 순간 스스로 언마운트되므로 넘겨받을 자리가 없으면 포커스가
   * `body`로 떨어진다(3차 리뷰 P1-1).
   */
  const assistantFocusTarget = useCallback(
    (): HTMLElement | null =>
      assistantCollapse.current ?? document.getElementById(assistantId),
    [assistantId],
  );
  const closeAssistant = useCallback((): void => {
    close(["assistantOpen"]);
    queueMicrotask(() =>
      document.getElementById(assistantToggleId)?.focus(),
    );
  }, [assistantToggleId, close]);
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
        execute: () => toggleRight("inspectorOpen"),
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
      ...(hasAssistant
        ? [
            {
              id: "panel.assistant",
              group: t("command.group.panel"),
              label: `${layout.assistantOpen ? t("command.hide") : t("command.show")} ${t("ide.assistant")}`,
              shortcut: "Alt A",
              focusAfterExecute: () =>
                layout.assistantOpen
                  ? assistantRestore.current
                  : assistantFocusTarget(),
              execute: () => toggleRight("assistantOpen"),
            },
          ]
        : []),
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
    assistantFocusTarget,
    availableViews,
    hasAssistant,
    layout,
    onRunBacktest,
    onSave,
    onSelectSymbol,
    onValidate,
    onViewChange,
    runDisabled,
    saveDisabled,
    symbols,
    theme,
    toggle,
    toggleRight,
    validateDisabled,
    view,
    viewTabsId,
  ]);

  useEffect(() => {
    if (narrow) close(["inspectorOpen", "debuggerOpen", "assistantOpen"]);
  }, [narrow, close]);

  // window 리스너는 layout effect로 설치한다(`.claude/rules/frontend-react-effects.md`, backlog 20·21).
  useLayoutEffect(() => {
    if (!narrow && overlayRight === null) return;
    const onKey = (event: KeyboardEvent) => {
      // 오버레이로 떠 있는 패널만 닫는다. 자리에 박혀 있는 패널은 Escape로 사라지지 않는다.
      if (event.key !== "Escape") return;
      close(
        narrow
          ? ["inspectorOpen", "debuggerOpen", "assistantOpen"]
          : [overlayRight as "inspectorOpen" | "assistantOpen"],
      );
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [narrow, overlayRight, close]);

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
      if (modifier && key === "s") {
        event.preventDefault();
        if (!event.repeat && !saveDisabled) onSave?.();
      } else if (modifier && event.key === "Enter") {
        event.preventDefault();
        if (event.repeat) return;
        if (event.shiftKey) {
          if (!runDisabled) onRunBacktest?.();
        } else if (!validateDisabled) onValidate?.();
      } else if (
        event.altKey &&
        !modifier &&
        (key === "a" || event.code === "KeyA")
      ) {
        // Alt+A: AI 사이드바 토글. `code`도 보는 이유는 Alt 조합에서 `key`가 자판에 따라 다른 글자로
        // 오기 때문이다(macOS 옵션 키).
        if (!hasAssistant || event.repeat) return;
        event.preventDefault();
        const opening = !layout.assistantOpen;
        toggleRight("assistantOpen");
        queueMicrotask(() =>
          (opening ? assistantFocusTarget() : assistantRestore.current)?.focus(),
        );
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
    assistantFocusTarget,
    availableViews,
    hasAssistant,
    layout.assistantOpen,
    onRunBacktest,
    onSave,
    onValidate,
    onViewChange,
    paletteOpen,
    runDisabled,
    saveDisabled,
    toggleRight,
    validateDisabled,
    viewTabsId,
  ]);

  const inspectorNode = (
    <aside
      id={ids.inspector}
      className="ide__inspector"
      aria-label={t("ide.inspector")}
      hidden={!layout.inspectorOpen}
      style={inspectorFloating ? undefined : { width: layout.inspectorWidth }}
    >
      <header className="ide__panel-header">
        <h2>{t("ide.inspector")}</h2>
        <Button
          ref={inspectorCollapse}
          size="small"
          tone="ghost"
          onClick={() => {
            toggleRight("inspectorOpen");
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

  /**
   * AI 어시스턴트 사이드바. 편집기 view와 무관하게 오른쪽 레일에 붙어 있어 YAML·Form·Graph 어느 탭에서도
   * 같은 대화가 보인다(spec D7). 1280px 미만에서는 계약·중간 결과와 같은 오버레이 서랍이 된다.
   */
  const assistantNode = hasAssistant ? (
    <aside
      id={ids.assistant}
      className="ide__assistant"
      aria-label={t("ide.assistant")}
      // 펼친 직후 포커스를 받을 수 있게 한다. 탭 순서에는 들어가지 않는다.
      tabIndex={-1}
      hidden={!layout.assistantOpen}
      style={assistantFloating ? undefined : { width: layout.assistantWidth }}
    >
      {/*
        패널의 landmark·이름·제목은 슬롯이 소유한다(WORKFLOW B-04 Acceptance). 슬롯 내용은 이름 없는
        `<section>`이라 여기서 이름을 달지 않으면 landmark 탐색으로 닿지 않는다.
        닫기는 한 곳만 그린다: 슬롯이 손잡이를 받는 함수면 그쪽이 닫기(진행 중 작업 확인 포함)를
        그리므로 여기서는 접기 버튼을 내지 않는다.
      */}
      <header className="ide__panel-header">
        <h2>{t("ide.assistant")}</h2>
        {typeof assistant === "function" ? null : (
          <Button
            ref={assistantCollapse}
            size="small"
            tone="ghost"
            onClick={() => {
              toggleRight("assistantOpen");
              queueMicrotask(() => assistantRestore.current?.focus());
            }}
          >
            {t("ide.collapseAssistant")}
          </Button>
        )}
      </header>
      {/* 본문은 한 겹 감싼다 — 슬롯이 요소를 여럿 넘겨도 패널 높이를 나눠 갖지 않는다(리뷰 P1-1). */}
      <div className="ide__assistant-body">
        {assistantMounted
          ? typeof assistant === "function"
            ? assistant({ close: closeAssistant })
            : assistant
          : null}
      </div>
    </aside>
  ) : null;

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
          {inspectorFloating || !layout.inspectorOpen ? (
            <Button
              ref={inspectorRestore}
              size="small"
              onClick={() => {
                toggleRight("inspectorOpen");
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
          {hasAssistant && (assistantFloating || !layout.assistantOpen) ? (
            <Button
              ref={assistantRestore}
              id={assistantToggleId}
              size="small"
              onClick={() => {
                toggleRight("assistantOpen");
                queueMicrotask(() => assistantFocusTarget()?.focus());
              }}
              aria-controls={ids.assistant}
              aria-expanded={layout.assistantOpen}
              aria-keyshortcuts="Alt+A"
            >
              {t("ide.assistant")}
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

        {!inspectorFloating && layout.inspectorOpen ? (
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
        {inspectorFloating ? null : inspectorNode}

        {hasAssistant && !assistantFloating && layout.assistantOpen ? (
          <SplitHandle
            orientation="vertical"
            label={t("ide.resizeAssistant")}
            value={layout.assistantWidth}
            min={PANEL_BOUNDS.assistantWidth.min}
            max={PANEL_BOUNDS.assistantWidth.max}
            invert
            onChange={(value) => resize("assistantWidth", value)}
            controls={ids.assistant}
          />
        ) : null}
        {assistantFloating ? null : assistantNode}
      </div>

      {inspectorFloating ? (
        <div className="ide__drawer" hidden={!layout.inspectorOpen}>
          {inspectorNode}
        </div>
      ) : null}
      {narrow ? (
        <div
          className="ide__drawer ide__drawer--bottom"
          hidden={!layout.debuggerOpen}
        >
          {debuggerNode}
        </div>
      ) : null}
      {hasAssistant && assistantFloating ? (
        <div className="ide__drawer" hidden={!layout.assistantOpen}>
          {assistantNode}
        </div>
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
