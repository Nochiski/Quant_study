import { t } from "../../../shared/config";
import { Badge, EmptyState } from "../../../shared/ui";

/** Shown only when the operations flag is on; makes clear nothing here places orders yet. */
export const OperationsPlaceholderPage = ({ area }: { area: string }) => (
  <>
    <header className="page-header">
      <h1>{area}</h1>
      <Badge tone="warn">{t("nav.operations.future")}</Badge>
    </header>
    <EmptyState
      title={t("page.operations.placeholderTitle")}
      description={t("page.operations.placeholder")}
    />
  </>
);
