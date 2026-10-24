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

import {
  STRATEGY_VIEWS,
  type StrategyOutlineSymbol,
  type StrategyView,
} from "../../../features/edit-strategy";
import { ASSISTANT_WINDOW_STORAGE_KEY } from "../../../entities/assistant";
import { t } from "../../../shared/config";
import { isSheetViewport } from "../../../shared/lib/floating-geometry";
import { useMediaQuery } from "../../../shared/lib/media";
import { useThemePreference } from "../../../shared/lib/theme";
import {
  Badge,
  Button,
  FloatingWindow,
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
import {
  OpenEditorWorkspaceContext,
  OpenTraceWorkspaceContext,
} from "../model/workspace-context";
import "./strategy-ide.css";

export type SourceView = StrategyView;

export type StrategyIdeProps = {
  title: string;
  /** Revision label shown next to the breadcrumb and the title, e.g. "v12" or "초안". */
  versionLabel: string;
  /** Header badges next to the title (draft/revision markers). */
  badges?: ReactNode;
  /** 현재 문서의 연구 진입점. 요청·계열 판정은 주입한 기능이 소유한다. */
  research?: ReactNode | ((controls: { openEditor: () => void }) => ReactNode);
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
  /** 저장 상태 소유자가 파생한 짧은 안내. 위젯은 오류색이나 상세 문자열로 상태를 추측하지 않는다. */
  saveStatusSummary?: string | null;
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
  sourceView?: "yaml";
  /** Stable read-only tab content keyed by representation. */
  projections?: Partial<Record<SourceView, ReactNode>>;
  /** Document-level recovery or warning UI that must remain visible across every view. */
  notice?: ReactNode;
  /**
   * 실행 설정 요약 띠(P3-02, 시안 1). 제목 아래 모든 탭 위에 두어, 지금 실행이 어떤 환경으로 나갈지와
   * 그 값이 전략 문서 밖이라는 것을 편집 중에 늘 보인다. 내용은 페이지가 주입한다.
   */
  runEnvironment?: ReactNode;
  /** Strategy document outline projection (P4-01). */
  outline: ReactNode | ((controls: { inspect: () => void }) => ReactNode);
  /** P4-10 catalog UI; the P4-05 feature model owns schema projection and insertion. */
  snippets: ReactNode;
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
  /** 문제 목록. 탭 패널 밖(편집 패널 아래)이라 두 표현에서 함께 보인다(WORKFLOW P1-01). */
  problems?: ReactNode;
  revisionDiff?: (active: boolean) => ReactNode;
  comparisonOpen?: boolean;
  onComparisonOpenChange?: (open: boolean) => void;
  view?: SourceView;
  onViewChange?: (view: SourceView) => void;
  /** Views the caller can render; the rest are shown disabled. */
  availableViews?: readonly SourceView[];
  /** 계약 검사 슬롯. */
  inspector: ReactNode;
  /** 실제 중간 결과 슬롯. */
  debugger: ReactNode;
  /**
   * 우측 AI 어시스턴트 사이드바 슬롯(B-04). 넘기지 않으면 패널도 토글도 만들지 않는다 — 어시스턴트를
   * 붙이지 않은 화면의 배치는 그대로다. 내용은 페이지가 주입한다(widget은 채팅을 알지 않는다).
   *
   * 함수를 넘기면 패널을 접는 손잡이를 받는다. 사이드바의 "닫기"가 진행 중 턴 취소를 확인한 뒤 패널을
   * 접는 경로다(spec D7). 패널 헤더의 접기 버튼은 대화를 끝내지 않는 패널 조작이라 확인을 거치지 않는다.
   */
  assistant?: ReactNode | ((controls: AssistantSlotControls) => ReactNode);
};

/**
 * 슬롯 함수가 받는 패널 손잡이. `open`은 지금 펼쳐졌는지다 — 한 번 펼친 뒤에는 접어도 내용이 마운트된 채
 * 남으므로, 접힌 동안 조회를 멈출 내용은 이것을 읽는다. 펼침의 정본은 이 widget 이다.
 */
export type AssistantSlotControls = { close: () => void; open: boolean };

const NARROW_QUERY = "(max-width: 1279px)";

/*
 * 브라우저가 스스로 텍스트 되돌리기를 갖는 입력 타입. select·button은 물론 날짜·시간 계열도 빠진다 —
 * 분절 위젯이라 되돌리기가 없어서, 양보하면 그 자리에서 Ctrl+Z가 아무 일도 하지 않는다(P1-02 리뷰 P3).
 */
const NATIVE_UNDO_INPUT_TYPES = new Set([
  "text",
  "search",
  "url",
  "tel",
  "email",
  "password",
  "number",
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

/**
 * 펼친 AI 사이드바로 포커스를 옮기는 방법 — 페이지를 굴리지 않는다. 제품 슬롯은 접기 버튼을 그리지 않아
 * 창보다 긴 패널 자신이 포커스를 받는데, 그냥 옮기면 브라우저가 패널 아래 끝을 창 아래 끝에 맞추려 페이지를
 * 굴려 서랍 머리 줄이 상단 바 밑에 깔렸다(#325, #290 리뷰 r3 P2-1). 여는 세 입구(상단 바 토글·Alt+A·명령
 * 팔레트)가 이 값 하나로 옮긴다.
 */
const ASSISTANT_FOCUS: FocusOptions = { preventScroll: true };
const VIEWS = STRATEGY_VIEWS;

/**
 * Strategy IDE frame laid out like the concept: top bar (breadcrumb, save status, run), title
 * with meta line, left Outline + Snippets, centre editor with format/validate actions and the
 * 그래프/YAML 표현, revision 비교, 오른쪽 계약 패널, 아래 중간 결과를 조합한다. All
 * panels resize and collapse; collapsed panels stay in the DOM (`hidden`) so every toggle's
 * `aria-controls` resolves. Below 1280px the inspector and debugger become non-modal drawers,
 * closed by default, toggled from the top bar and dismissed with Escape. There is deliberately
 * no top Data→…→Execution stepper: the outline is the only navigation.
 */
export const StrategyIde = ({
  title,
  versionLabel,
  badges,
  research,
  meta,
  saveStatus,
  saveStatusSummary,
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
  runEnvironment,
  outline,
  snippets,
  editorActions,
  documentHistory,
  documentStatus,
  problems,
  revisionDiff,
  comparisonOpen = false,
  onComparisonOpenChange,
  view = "graph",
  onViewChange,
  availableViews = VIEWS,
  inspector,
  debugger: debuggerPanel,
  assistant,
}: StrategyIdeProps) => {
  const narrow = useMediaQuery(NARROW_QUERY);
  const compact = useMediaQuery("(max-width: 767px)");
  // 좁은 화면(창이 하단 시트가 되는 폭)에서는 열림 기록이 없으면 AI 창을 최소화한 채 시작한다 — 시트가 편집기를
  // 거의 다 덮는다. 기록이 있으면 기록이 이긴다(`usePanelLayout` 이 저장값을 기본값 위에 덮는다).
  const { layout, resize, toggle, close } = usePanelLayout({
    ...DEFAULT_LAYOUT,
    assistantOpen:
      DEFAULT_LAYOUT.assistantOpen &&
      !isSheetViewport({ width: window.innerWidth, height: window.innerHeight }),
  });
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
  const hasAssistant = assistant !== undefined;
  // AI 어시스턴트는 본문 열이 아니라 화면 위에 떠 있는 창이다(FloatingWindow). 오른쪽 계약 패널만 좁은 화면·추적
  // 작업공간에서 서랍으로 뜬다 — AI 창과 자리를 다투지 않는다.
  const inspectorFloating = layout.debuggerOpen || narrow;
  /**
   * 오른쪽 패널 토글. 좁은 화면에서는 계약·AI 서랍이 같은 자리(본문 오른쪽 끝)에 뜨므로
   * 한 번에 하나만 연다 — 겹치면 뒤에 깔린 패널이 보이지 않은 채 탭 순서와 접근성 트리에 남는다
   * (B-04 리뷰 P1-2).
   */
  const togglePanel = useCallback(
    (panel: "outlineOpen" | "inspectorOpen" | "debuggerOpen" | "assistantOpen"): void => {
      const opening = !layout[panel];
      if (opening && panel === "outlineOpen") close(["debuggerOpen"]);
      if (opening && panel === "debuggerOpen") close(["outlineOpen", "inspectorOpen"]);
      // 작은 화면의 보조 서랍은 하나만 펼쳐 가려진 내용이 탭 순서에 남지 않게 한다. AI 창은 떠 있는 창이라
      // 서랍과 자리를 다투지 않는다.
      if (opening && compact && panel !== "assistantOpen") {
        close((["outlineOpen", "inspectorOpen"] as const).filter((key) => key !== panel));
      }
      toggle(panel);
    },
    [close, compact, layout, toggle],
  );
  const openTraceWorkspace = useCallback(() => {
    if (!layout.debuggerOpen) togglePanel("debuggerOpen");
  }, [layout.debuggerOpen, togglePanel]);
  const inspect = useCallback((focusInspector = true) => {
    if (!layout.inspectorOpen) toggle("inspectorOpen");
    if (compact) close(["outlineOpen"]);
    queueMicrotask(() => {
      const body = document.getElementById(`${inspectorId}-body`);
      if (body !== null) body.scrollTop = 0;
      if (compact && focusInspector) document.getElementById(`${inspectorId}-collapse`)?.focus({ preventScroll: true });
    });
  }, [close, compact, layout.inspectorOpen, toggle, inspectorId]);

  // 슬롯 함수는 렌더 중에 불리므로 이 손잡이는 ref를 닫지 않는다(`react-hooks/refs`). 접은 뒤 돌아갈
  // 자리는 창의 최소화 런처이고, 그 자리는 id로 찾는다.
  const assistantToggleId = `${assistantId}-toggle`;
  /**
   * 사이드바를 펼친 뒤 포커스가 갈 자리. 슬롯이 접기 버튼을 그리면 그 버튼, 아니면 패널 자신이다
   * (`tabIndex={-1}`) — 상단 토글은 펼치는 순간 스스로 언마운트되므로 넘겨받을 자리가 없으면 포커스가
   * `body`로 떨어진다(3차 리뷰 P1-1).
   */
  const assistantFocusTarget = useCallback(
    (): HTMLElement | null => document.getElementById(assistantId),
    [assistantId],
  );
  /** 창을 닫은 뒤 초점이 돌아갈 자리: 우하단 런처. */
  const assistantLauncher = useCallback(
    (): HTMLElement | null => document.getElementById(assistantToggleId),
    [assistantToggleId],
  );
  const closeAssistant = useCallback((): void => {
    close(["assistantOpen"]);
    queueMicrotask(() => document.getElementById(assistantToggleId)?.focus());
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
      execute: () => { close(["debuggerOpen"]); onViewChange?.(id); },
    }));
    const panelCommands: CommandPaletteItem[] = [
      {
        id: "panel.outline",
        group: t("command.group.panel"),
        label: `${layout.outlineOpen ? t("command.hide") : t("command.show")} ${t("ide.outline")}`,
        focusAfterExecute: () =>
          layout.outlineOpen ? outlineRestore.current : outlineCollapse.current,
        execute: () => togglePanel("outlineOpen"),
      },
      {
        id: "panel.inspector",
        group: t("command.group.panel"),
        label: `${layout.inspectorOpen ? t("command.hide") : t("command.show")} ${t("ide.inspector")}`,
        focusAfterExecute: () =>
          layout.inspectorOpen
            ? inspectorRestore.current
            : inspectorCollapse.current,
        execute: () => togglePanel("inspectorOpen"),
      },
      {
        id: "panel.debugger",
        group: t("command.group.panel"),
        label: `${layout.debuggerOpen ? t("command.hide") : t("command.show")} ${t("ide.debugger")}`,
        focusAfterExecute: () =>
          layout.debuggerOpen
            ? debuggerRestore.current
            : debuggerCollapse.current,
        execute: () => togglePanel("debuggerOpen"),
      },
      ...(hasAssistant
        ? [
            {
              id: "panel.assistant",
              group: t("command.group.panel"),
              label: `${layout.assistantOpen ? t("command.hide") : t("command.show")} ${t("ide.assistant")}`,
              shortcut: "Alt A",
              focusAfterExecute: () =>
                layout.assistantOpen ? assistantLauncher() : assistantFocusTarget(),
              focusOptions: ASSISTANT_FOCUS,
              execute: () => togglePanel("assistantOpen"),
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
      execute: () => {
        close(["debuggerOpen"]);
        // 심볼 명령은 원문 이동이다. 떠 있는 보조 패널 뒤에 편집 초점을 남기지 않는다.
        if (narrow) close(["outlineOpen", "inspectorOpen", "assistantOpen"]);
        onSelectSymbol?.(symbol.pointer);
        if (!narrow) inspect(false);
      },
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
    assistantLauncher,
    availableViews,
    close,
    hasAssistant,
    inspect,
    layout,
    narrow,
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
    togglePanel,
    validateDisabled,
    view,
    viewTabsId,
  ]);

  useEffect(() => {
    if (narrow) close(["inspectorOpen"]);
  }, [narrow, close]);

  // window 리스너는 layout effect로 설치한다(`.claude/rules/frontend-react-effects.md`, backlog 20·21).
  useLayoutEffect(() => {
    if (!narrow) return;
    const onKey = (event: KeyboardEvent) => {
      // 오버레이로 떠 있는 계약 서랍만 닫는다. 자리에 박혀 있는 패널과 떠 있는 AI 창은 Escape로 사라지지 않는다.
      if (event.key !== "Escape") return;
      close(["inspectorOpen"]);
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
        togglePanel("assistantOpen");
        queueMicrotask(() =>
          (opening ? assistantFocusTarget() : assistantLauncher())?.focus(ASSISTANT_FOCUS),
        );
      } else if (event.altKey && !modifier && /^[1-2]$/.test(event.key)) {
        const next = VIEWS[Number(event.key) - 1];
        if (
          event.repeat ||
          !next ||
          !availableViews.includes(next) ||
          !onViewChange
        )
          return;
        event.preventDefault();
        close(["debuggerOpen"]);
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
    assistantLauncher,
    availableViews,
    close,
    hasAssistant,
    layout.assistantOpen,
    onRedo,
    onRunBacktest,
    onSave,
    onUndo,
    onValidate,
    onViewChange,
    paletteOpen,
    runDisabled,
    saveDisabled,
    togglePanel,
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
          id={`${inspectorId}-collapse`}
          size="small"
          tone="ghost"
          onClick={() => {
            togglePanel("inspectorOpen");
            queueMicrotask(() => inspectorRestore.current?.focus());
          }}
        >
          {t("ide.collapseInspector")}
        </Button>
      </header>
      <div id={`${inspectorId}-body`} className="ide__inspector-body">{inspector}</div>
    </aside>
  );

  const debuggerNode = (
    <section
      id={ids.debugger}
      className="ide__debugger"
      aria-label={t("ide.debugger")}
      hidden={!layout.debuggerOpen}
      data-workspace="trace"
    >
      <header className="ide__panel-header">
        <h2>{t("ide.debugger")}</h2>
        <Button
          ref={debuggerCollapse}
          size="small"
          tone="ghost"
          onClick={() => {
            togglePanel("debuggerOpen");
            queueMicrotask(() => debuggerRestore.current?.focus());
          }}
        >
          {t("ide.workspace.return")}
        </Button>
      </header>
      <OpenEditorWorkspaceContext.Provider value={() => close(["debuggerOpen"])}>
        {debuggerPanel}
      </OpenEditorWorkspaceContext.Provider>
    </section>
  );

  /**
   * AI 어시스턴트 창. 편집기 view와 무관하게 화면 위에 떠 있어 YAML·Graph 어느 탭에서도 같은 대화가 보인다(spec D7).
   * 열림의 owner 는 이 widget 의 패널 배치(`assistantOpen`)이고, 슬롯 함수가 그 값(`open`)을 받아 닫힌 동안 조회를
   * 멈춘다. 창은 처음 열 때 본문을 마운트하고 최소화해도 유지한다 — 진행 중 턴의 스트림이 끊기지 않는다. 위치·크기는
   * 새 전략 안내의 AI 창과 같은 보관함 키로 기억한다.
   *
   * 닫기는 두 곳이다: 창의 최소화 버튼은 대화를 끝내지 않는 패널 조작이고, 슬롯이 손잡이를 받는 함수면 그쪽의
   * 닫기가 진행 중 작업을 확인한 뒤 접는다(spec D7).
   */
  const assistantNode = hasAssistant ? (
    <FloatingWindow
      id={ids.assistant}
      title={t("ide.assistant")}
      open={layout.assistantOpen}
      onOpenChange={(open) => {
        if (open !== layout.assistantOpen) togglePanel("assistantOpen");
      }}
      storageKey={ASSISTANT_WINDOW_STORAGE_KEY}
      launcherLabel={t("ide.assistant")}
      launcherId={assistantToggleId}
      minimizeLabel={t("ide.collapseAssistant")}
    >
      {typeof assistant === "function"
        ? assistant({ close: closeAssistant, open: layout.assistantOpen })
        : assistant}
    </FloatingWindow>
  ) : null;

  return (
    <div className={`ide${layout.debuggerOpen ? " ide--trace" : ""}`}>
      <header className="ide__topbar">
        <nav className="ide__breadcrumb" aria-label={t("ide.breadcrumb")}>
          <span className="ide__crumb">{t("ide.breadcrumb.strategies")}</span>
          <span className="ide__crumb-sep" aria-hidden="true">
            /
          </span>
          <div className="ide__crumb ide__crumb--current" aria-current="page">
            <h1>{title}</h1>
            {badges ?? <Badge tone="neutral">{versionLabel}</Badge>}
          </div>
          {research === undefined ? <Badge tone="accent">{t("nav.research")}</Badge> : typeof research === "function" ? research({ openEditor: () => close(["debuggerOpen"]) }) : research}
        </nav>
        {saveStatusSummary ? <span className="ide__save-error" role="status">{saveStatusSummary}</span> : null}
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
          {compact || !layout.outlineOpen ? (
            <Button
              ref={outlineRestore}
              size="small"
              onPointerDown={(event) => {
                // 열린 서랍의 blur가 click보다 먼저 닫아 같은 버튼을 다시 여는 것을 막는다.
                if (compact && layout.outlineOpen) event.preventDefault();
              }}
              onClick={() => {
                const opening = !layout.outlineOpen;
                togglePanel("outlineOpen");
                queueMicrotask(() => (opening ? outlineCollapse.current : outlineRestore.current)?.focus());
              }}
              aria-label={t("ide.outline")}
              aria-controls={ids.outline}
              aria-expanded={layout.outlineOpen}
            >
              <span className="ide__label-full">{t("ide.outline")}</span><span className="ide__label-short" aria-hidden="true">{t("ide.outline.short")}</span>
            </Button>
          ) : null}
          {inspectorFloating || !layout.inspectorOpen ? (
            <Button
              ref={inspectorRestore}
              size="small"
              onClick={() => {
                togglePanel("inspectorOpen");
                queueMicrotask(() => inspectorCollapse.current?.focus());
              }}
              aria-controls={ids.inspector}
              aria-expanded={layout.inspectorOpen}
            >
              {t("ide.inspector")}
            </Button>
          ) : null}
        <details
          className="ide__document-info ui-disclosure"
          onBlurCapture={(event) => {
            if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false;
          }}
        >
          <summary aria-label={t("ide.documentInfo")}><span className="ide__label-full">{t("ide.documentInfo")}</span><span className="ide__label-short" aria-hidden="true">{t("ide.documentInfo.short")}</span></summary>
        <dl className="ide__meta">
          {saveStatus ? <div><dt>{t("ide.meta.status")}</dt><dd role="status" data-tone={saveTone}>{saveStatus}</dd></div> : null}
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
                  {meta.sourceHash ?? "—"}
                </code>
              </dd>
            </div>
          ) : null}
          {meta?.specHash !== undefined ? (
            <div>
              <dt>{t("ide.meta.specHash")}</dt>
              <dd>
                <code title={meta.specHash ?? undefined}>
                  {meta.specHash ?? "—"}
                </code>
              </dd>
            </div>
          ) : null}
        </dl>
        </details>
        </div>
      </header>

      <div className="ide__document-bar">
        <div className="ide__document-actions">{editorActions}</div>

      </div>

      <div className="ide__workspace-bar">
        <div role="group" aria-label={t("ide.workspace.label")} className="ide__workspace-switch">
          <Button
            size="small"
            aria-pressed={!layout.debuggerOpen}
            onClick={() => {
              if (layout.debuggerOpen) togglePanel("debuggerOpen");
            }}
          >{t("ide.workspace.edit")}</Button>
          <Button
            ref={debuggerRestore}
            size="small"
            aria-pressed={layout.debuggerOpen}
            onClick={() => {
              if (!layout.debuggerOpen) togglePanel("debuggerOpen");
            }}
          >{t("ide.debugger")}</Button>
        </div>
        {documentStatus ? <div className="ide__workspace-status">{documentStatus}</div> : null}
      </div>

      {runEnvironment ?? null}
      {notice ? <div className="ide__notice">{notice}</div> : null}

      <OpenTraceWorkspaceContext.Provider value={openTraceWorkspace}>
      <div className="ide__body">
        {/* AI 창은 화면 위에 떠 있다. 늘 같은 자리에 두어 DOM 부모·순서를 바꾸지 않는다 — 대화·미전송 입력·스트림
            소유자를 재마운트하지 않는다. */}
        {assistantNode}
        <div
          className={`ide__left${compact ? " ide__left--drawer" : ""}`}
          hidden={!layout.outlineOpen || layout.debuggerOpen}
          style={compact ? undefined : { width: layout.outlineWidth }}
          onBlurCapture={(event) => {
            if (compact && event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget)) close(["outlineOpen"]);
          }}
          onKeyDown={(event) => {
            if (!compact || event.key !== "Escape") return;
            event.preventDefault();
            event.stopPropagation();
            close(["outlineOpen"]);
            queueMicrotask(() => outlineRestore.current?.focus());
          }}
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
                  togglePanel("outlineOpen");
                  queueMicrotask(() => outlineRestore.current?.focus());
                }}
              >
                {t("ide.collapseOutline")}
              </Button>
            </header>
            {typeof outline === "function" ? outline({ inspect }) : outline}
          </nav>
          <section className="ide__snippets" aria-label={t("ide.snippets")}>
            <h2 className="ide__snippets-title">{t("ide.snippets")}</h2>
            {snippets}
          </section>
        </div>
        {layout.outlineOpen && !compact && !layout.debuggerOpen ? (
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
          <section className="ide__editor" aria-label={t("ide.editor")} hidden={layout.debuggerOpen}>
            <div className="ide__editor-tabs">
              <Tabs
                label={t("ui.tabs.view")}
                idBase={ids.views}
                items={VIEWS.map((id) => ({
                  id,
                  label: id === "yaml" ? "YAML" : t("ide.view.graph"),
                  disabled: !availableViews.includes(id),
                }))}
                value={view}
                onChange={(next) => onViewChange?.(next)}
              />
              {documentHistory ? (
                <div className="ide__editor-history">{documentHistory}</div>
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
            {revisionDiff ? (
              <section className="ide__revision-diff">
                <Button
                  aria-expanded={comparisonOpen}
                  onClick={() => onComparisonOpenChange?.(!comparisonOpen)}
                >
                  {t("ide.revision.diff")}
                </Button>
                <div hidden={!comparisonOpen}>{revisionDiff(comparisonOpen)}</div>
              </section>
            ) : null}
            {problems ? <div className="ide__problems">{problems}</div> : null}
          </section>
          {debuggerNode}
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


        {/* 서랍은 본문 안에 그린다 — 오른쪽 서랍의 세로 범위가 본문과 같아 상단 바 밑에 깔리지 않는다(#325). */}
        {inspectorFloating ? (
          <div className="ide__drawer" hidden={!layout.inspectorOpen}>
            {inspectorNode}
          </div>
        ) : null}

      </div>
      </OpenTraceWorkspaceContext.Provider>
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
