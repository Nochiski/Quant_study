import {
  SourceEditor,
  useStrategyDocument,
} from "../../../features/edit-strategy";
import { t } from "../../../shared/config";
import { Badge } from "../../../shared/ui";
import { StrategyIde } from "../../../widgets/strategy-ide";

const STARTER = 'schema_version: "1.0"\ntitle: ""\n';

/**
 * New-strategy entry: the IDE frame with the source editor bound to the document state machine.
 * The revision-aware loader (P2-04) and toolbar/save (P3-05) build on this.
 */
export const NewStrategyPage = () => {
  const [document, dispatch] = useStrategyDocument("yaml", STARTER);
  return (
    <StrategyIde
      title={t("page.newStrategy.title")}
      badges={<Badge tone="info">{t("page.newStrategy.draft")}</Badge>}
      editor={<SourceEditor state={document} dispatch={dispatch} />}
    />
  );
};
