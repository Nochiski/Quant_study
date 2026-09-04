import { useDatasetCatalog } from "../../../entities/dataset";
import { t } from "../../../shared/config";
import { StrategyEditor } from "../../../widgets/strategy-editor";

export const StrategyBuilderPage = () => {
  const catalog = useDatasetCatalog({ page_size: 1 });

  return (
    <div className="legacy-builder">
      <header className="hero">
        <div>
          <span className="eyebrow">{t("builder.eyebrow")}</span>
          <h1>{t("builder.title")}</h1>
          <p>{t("builder.subtitle")}</p>
        </div>
        <dl className="contract-status">
          <div>
            <dt>Schema</dt>
            <dd>StrategySpec v1</dd>
          </div>
          <div>
            <dt>Equity mock</dt>
            <dd>{catalog.data?.total ?? "—"} fields</dd>
          </div>
          <div>
            <dt>Execution</dt>
            <dd>Persistent Rust</dd>
          </div>
        </dl>
      </header>
      <StrategyEditor />
    </div>
  );
};
