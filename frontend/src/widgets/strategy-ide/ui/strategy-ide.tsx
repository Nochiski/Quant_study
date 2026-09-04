import { useEffect, useId, useState, type ReactNode } from "react";

import { t } from "../../../shared/config";
import { useMediaQuery } from "../../../shared/lib/media";
import {
  Badge,
  Button,
  SplitHandle,
  Tabs,
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
  /** The source editor slot (P3). */
  editor: ReactNode;
  /** Strategy document outline projection (P4-01). */
  outline?: ReactNode;
  /** Editor toolbar actions (format / validate) rendered in the editor header. */
  editorActions?: ReactNode;
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
  editor,
  outline,
  editorActions,
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
  const ids = {
    outline: useId(),
    inspector: useId(),
    debugger: useId(),
    views: useId(),
  };

  useEffect(() => {
    if (narrow) close(["inspectorOpen", "debuggerOpen"]);
  }, [narrow, close]);

  useEffect(() => {
    if (!narrow) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close(["inspectorOpen", "debuggerOpen"]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [narrow, close]);

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
          size="small"
          tone="ghost"
          onClick={() => toggle("inspectorOpen")}
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
          size="small"
          tone="ghost"
          onClick={() => toggle("debuggerOpen")}
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
          {!layout.outlineOpen ? (
            <Button
              size="small"
              onClick={() => toggle("outlineOpen")}
              aria-controls={ids.outline}
              aria-expanded={false}
            >
              {t("ide.outline")}
            </Button>
          ) : null}
          {narrow || !layout.inspectorOpen ? (
            <Button
              size="small"
              onClick={() => toggle("inspectorOpen")}
              aria-controls={ids.inspector}
              aria-expanded={layout.inspectorOpen}
            >
              {t("ide.inspector")}
            </Button>
          ) : null}
          {narrow || !layout.debuggerOpen ? (
            <Button
              size="small"
              onClick={() => toggle("debuggerOpen")}
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
                size="small"
                tone="ghost"
                onClick={() => toggle("outlineOpen")}
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
            {VIEWS.map((id) => (
              <div
                key={id}
                id={panelId(ids.views, id)}
                role="tabpanel"
                aria-labelledby={tabId(ids.views, id)}
                className="ide__editor-panel"
                hidden={id !== view}
              >
                {id === view ? editor : null}
              </div>
            ))}
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
