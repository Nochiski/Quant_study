import { useEffect } from "react";

import {
  DirtyLeaveGuard,
  SaveAction,
  SourceEditor,
  saveStatusText,
  useCompileDocument,
  useSaveDocument,
  useSchemaAssist,
  useStrategyDocument,
  type DocumentSource,
} from "../../../features/edit-strategy";
import { t } from "../../../shared/config";
import { useNavigate } from "../../../shared/lib/router";
import { Badge } from "../../../shared/ui";
import { StrategyIde } from "../../../widgets/strategy-ide";

const STARTER = 'schema_version: "1.0"\ntitle: ""\n';
const NEW_DRAFT: DocumentSource = {
  kind: "new",
  format: "yaml",
  source: STARTER,
};

/**
 * New-strategy entry (WORKFLOW P2-04): a draft with no base. Saving creates the strategy, after
 * which the URL moves to revision 1 so a reload lands on the saved document (router ADR D3).
 */
export const NewStrategyPage = () => {
  const navigate = useNavigate();
  const [document, dispatch] = useStrategyDocument(NEW_DRAFT);
  const { save, status, canSave } = useSaveDocument(document, dispatch);
  const assist = useSchemaAssist(document);
  useCompileDocument(document, dispatch);

  useEffect(() => {
    if (document.strategyId === null || document.baseRevision === null) return;
    void navigate({
      to: "/research/strategies/$strategyId/revisions/$revision",
      params: {
        strategyId: document.strategyId,
        revision: String(document.baseRevision),
      },
      search: {
        view: undefined,
        path: undefined,
        asOf: undefined,
        security: undefined,
      },
      replace: true,
    });
  }, [document.strategyId, document.baseRevision, navigate]);

  return (
    <>
      <StrategyIde
        title={t("page.newStrategy.title")}
        versionLabel={t("page.newStrategy.draft")}
        badges={<Badge tone="info">{t("page.newStrategy.draft")}</Badge>}
        saveStatus={saveStatusText(document, status)}
        view={document.format}
        availableViews={[document.format]}
        editorActions={
          <SaveAction
            canSave={canSave}
            saving={status.kind === "saving"}
            onSave={save}
          />
        }
        editor={
          <SourceEditor state={document} dispatch={dispatch} assist={assist} />
        }
        runDisabled
      />
      <DirtyLeaveGuard dirty={document.dirty} />
    </>
  );
};
