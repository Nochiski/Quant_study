import { t } from "../../../shared/config";
import { Link } from "../../../shared/lib/router";
import { Badge } from "../../../shared/ui";
import { StrategyIde } from "../../../widgets/strategy-ide";

/**
 * New-strategy entry: the concept frame with placeholder content. The source editor (P3) and the
 * revision-aware draft state (P2-04) replace the placeholders without changing the layout.
 */
export const NewStrategyPage = () => (
  <StrategyIde
    title={t("page.newStrategy.title")}
    versionLabel={t("page.newStrategy.draft")}
    badges={<Badge tone="info">{t("page.newStrategy.draft")}</Badge>}
    editor={
      <div className="ide__inspector-body">
        <p>{t("page.newStrategy.placeholder")}</p>
        <Link
          to="/legacy/builder"
          search={{}}
          className="ui-button ui-button--secondary"
        >
          {t("nav.legacyBuilder")}
        </Link>
      </div>
    }
  />
);
