import { useState } from "react";

import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import { useStrategyDraft } from "../model/strategy-draft-context";
import { AdvancedGraph } from "./advanced-graph";
import { QuickEditor } from "./quick-editor";

export const StrategyEditorWorkspace = () => {
  const [mode, setMode] = useState<"quick" | "advanced">("quick");
  const { dirty, savedRevision, validation, pending, notice, validate, save } =
    useStrategyDraft();

  return (
    <div className="factor-workspace">
      <div className="editor-toolbar">
        <div className="mode-tabs" role="tablist" aria-label="Editor mode">
          <button
            aria-selected={mode === "quick"}
            onClick={() => setMode("quick")}
            role="tab"
            type="button"
          >
            {t("builder.mode.quick")}
          </button>
          <button
            aria-selected={mode === "advanced"}
            onClick={() => setMode("advanced")}
            role="tab"
            type="button"
          >
            {t("builder.mode.advanced")}
          </button>
        </div>
        <span
          className={`draft-state draft-state--${dirty ? "dirty" : "clean"}`}
        >
          {dirty ? t("builder.dirty") : t("builder.clean")}
        </span>
        <Button disabled={pending} onClick={() => void validate()}>
          {t("builder.validate")}
        </Button>
        <Button
          disabled={pending || !dirty}
          onClick={() => void save()}
          tone="primary"
        >
          {savedRevision === null
            ? t("builder.save")
            : t("builder.saveRevision")}
        </Button>
      </div>

      {mode === "quick" ? <QuickEditor /> : <AdvancedGraph />}

      <aside className="validation-panel" aria-live="polite">
        {notice !== null && <p className="notice">{notice}</p>}
        {validation !== null && (
          <>
            <strong>
              {validation.valid ? t("builder.valid") : t("builder.invalid")}
            </strong>
            <ul>
              {validation.issues.map((issue) => (
                <li key={`${issue.code}-${issue.path}`}>{issue.message}</li>
              ))}
            </ul>
          </>
        )}
      </aside>
    </div>
  );
};
