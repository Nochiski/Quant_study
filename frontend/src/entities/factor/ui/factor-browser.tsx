import type { FactorDefinition } from "../../../shared/api";
import { t } from "../../../shared/config";
import { FactorCard } from "./factor-card";

type FactorBrowserProps = {
  factors: FactorDefinition[];
  loading: boolean;
  search: string;
  selectedIds: ReadonlySet<string>;
  total: number;
  registryVersion?: string;
  onAdd: (factor: FactorDefinition) => void;
  onSearch: (search: string) => void;
};

export const FactorBrowser = ({
  factors,
  loading,
  search,
  selectedIds,
  total,
  registryVersion,
  onAdd,
  onSearch,
}: FactorBrowserProps) => (
  <section className="factor-browser" aria-labelledby="factor-catalog-title">
    <header>
      <div>
        <span className="section-kicker">{t("factor.catalog.kicker")}</span>
        <h2 id="factor-catalog-title">{t("factor.catalog.title")}</h2>
        <p>{t("factor.catalog.description")}</p>
      </div>
      <span className="registry-badge">
        {registryVersion ?? "registry"} · {total}
      </span>
    </header>
    <label className="factor-search">
      <span>{t("factor.catalog.search")}</span>
      <input
        onChange={(event) => onSearch(event.target.value)}
        placeholder={t("factor.catalog.searchPlaceholder")}
        type="search"
        value={search}
      />
    </label>
    {loading ? (
      <p className="inline-state">{t("factor.catalog.loading")}</p>
    ) : (
      <div className="factor-catalog-grid">
        {factors.map((factor) => (
          <FactorCard
            factor={factor}
            key={factor.factor_id}
            onAdd={onAdd}
            selected={selectedIds.has(factor.factor_id)}
          />
        ))}
      </div>
    )}
  </section>
);
