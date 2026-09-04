import { useEffect, useId, useState, type ReactNode } from "react";

import { t } from "../../../shared/config";
import { useMediaQuery } from "../../../shared/lib/media";
import { Badge, Button, SplitHandle, Tabs, panelId } from "../../../shared/ui";
import {
  OUTLINE_SECTIONS,
  type OutlineSection,
} from "../model/outline-sections";
import {
  DEFAULT_LAYOUT,
  PANEL_BOUNDS,
  usePanelLayout,
} from "../model/use-panel-layout";
import "./strategy-ide.css";

export type StrategyIdeProps = {
  title: string;
  /** Header badges, e.g. draft/revision markers. */
  badges?: ReactNode;
  /** The source editor slot (P3); a placeholder until then. */
  editor: ReactNode;
  /** Contract Inspector slot (P4); placeholder until then. */
  inspector?: ReactNode;
  /** Intermediate Debugger slot (P5); placeholder until then. */
  debugger?: ReactNode;
  /** Section shown as current in the outline (URL-owned by the page later). */
  currentSection?: OutlineSection;
  onSelectSection?: (section: OutlineSection) => void;
};

const NARROW_QUERY = "(max-width: 1279px)";

/**
 * Strategy IDE frame: left Outline, centre editor, right Contract Inspector, bottom Intermediate
 * Debugger, all resizable and collapsible. Collapsed panels stay in the DOM (`hidden`) so every
 * toggle's `aria-controls` resolves. Below 1280px the inspector and debugger become non-modal
 * off-canvas drawers, closed by default, toggled from the header and dismissed with Escape.
 * There is deliberately no top Data→…→Execution stepper: the outline is the only navigation.
 */
export const StrategyIde = ({
  title,
  badges,
  editor,
  inspector,
  debugger: debuggerPanel,
  currentSection,
  onSelectSection,
}: StrategyIdeProps) => {
  const narrow = useMediaQuery(NARROW_QUERY);
  const { layout, resize, toggle, close } = usePanelLayout(
    narrow
      ? { ...DEFAULT_LAYOUT, inspectorOpen: false, debuggerOpen: false }
      : DEFAULT_LAYOUT,
  );
  const ids = { outline: useId(), inspector: useId(), debugger: useId() };

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
      {inspector ?? <InspectorPlaceholder />}
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
      <header className="ide__header">
        <div className="ide__title">
          <h1>{title}</h1>
          {badges}
        </div>
        <div className="ide__actions">
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
        </div>
      </header>

      <div className="ide__body">
        <nav
          id={ids.outline}
          className="ide__outline"
          aria-label={t("ide.outline")}
          hidden={!layout.outlineOpen}
          style={{ width: layout.outlineWidth }}
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
          <ul className="ide__sections">
            {OUTLINE_SECTIONS.map((section) => (
              <li key={section}>
                <button
                  type="button"
                  className="ide__section"
                  aria-current={
                    section === currentSection ? "location" : undefined
                  }
                  onClick={() => onSelectSection?.(section)}
                >
                  <code>/{section}</code>
                  <Badge tone="neutral">{t("ide.section.pending")}</Badge>
                </button>
              </li>
            ))}
          </ul>
        </nav>
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
            {editor}
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

const INSPECTOR_TABS = [
  { id: "schema", label: t("ide.inspector.schema") },
  { id: "errors", label: t("ide.inspector.errors") },
  { id: "plan", label: t("ide.inspector.plan") },
] as const;

type InspectorTab = (typeof INSPECTOR_TABS)[number]["id"];

const InspectorPlaceholder = () => {
  const idBase = useId();
  const [tab, setTab] = useState<InspectorTab>("schema");
  return (
    <div className="ide__placeholder">
      <Tabs
        label={t("ide.inspector")}
        items={INSPECTOR_TABS}
        value={tab}
        onChange={setTab}
        idBase={idBase}
      />
      {INSPECTOR_TABS.map((item) => (
        <div
          key={item.id}
          id={panelId(idBase, item.id)}
          role="tabpanel"
          hidden={item.id !== tab}
        >
          {item.id === "schema" ? (
            <dl className="ide__contract">
              <dt>{t("ide.inspector.path")}</dt>
              <dd>
                <code>/risk/max_name_weight</code>
              </dd>
              <dt>{t("ide.inspector.unit")}</dt>
              <dd>ratio</dd>
              <dt>{t("ide.inspector.stage")}</dt>
              <dd>risk</dd>
            </dl>
          ) : (
            <p className="text-small">{t("ide.placeholder")}</p>
          )}
        </div>
      ))}
      <p className="text-small">{t("ide.placeholder")}</p>
    </div>
  );
};

const DebuggerPlaceholder = () => (
  <div className="ide__placeholder">
    <table className="ide__table">
      <caption className="text-small">{t("ide.placeholder")}</caption>
      <thead>
        <tr>
          <th scope="col">{t("ide.debugger.security")}</th>
          <th scope="col">{t("ide.debugger.before")}</th>
          <th scope="col">{t("ide.debugger.after")}</th>
          <th scope="col">{t("ide.debugger.status")}</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>—</td>
          <td>—</td>
          <td>—</td>
          <td>
            <Badge tone="neutral">{t("ide.section.pending")}</Badge>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
);
