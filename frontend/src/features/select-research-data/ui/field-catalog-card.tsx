import { useState } from "react";

import { useDatasetCatalog } from "../../../entities/dataset";
import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import type { LagOverrides } from "../model/research-data-selection";

type FieldCatalogCardProps = {
  selectedFieldIds: string[];
  lagOverrides: LagOverrides;
  onToggleField: (fieldId: string) => void;
  onLagOverride: (fieldId: string, sessions: string) => void;
};

export const FieldCatalogCard = ({
  selectedFieldIds,
  lagOverrides,
  onToggleField,
  onLagOverride,
}: FieldCatalogCardProps) => {
  const [search, setSearch] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [page, setPage] = useState(1);
  const catalog = useDatasetCatalog({
    search: search || undefined,
    dataset_id: datasetId ? [datasetId] : undefined,
    page,
    page_size: 4,
  });

  return (
    <section className="data-card">
      <div className="data-card__title">
        <span>02</span>
        <div>
          <h3>{t("dataset.catalog.title")}</h3>
          <p>{t("dataset.catalog.description")}</p>
        </div>
      </div>
      <div className="catalog-controls">
        <label>
          <span>{t("dataset.catalog.search")}</span>
          <input
            placeholder={t("dataset.catalog.searchPlaceholder")}
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
          />
        </label>
        <label>
          <span>{t("dataset.catalog.dataset")}</span>
          <select
            value={datasetId}
            onChange={(event) => {
              setDatasetId(event.target.value);
              setPage(1);
            }}
          >
            <option value="">{t("dataset.catalog.all")}</option>
            {catalog.data?.facets.dataset_ids.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="field-catalog" aria-live="polite">
        {catalog.data?.fields.map((field) => {
          const selected = selectedFieldIds.includes(field.field_id);
          return (
            <article className="field-profile" key={field.field_id}>
              <label className="field-profile__select">
                <input
                  checked={selected}
                  onChange={() => onToggleField(field.field_id)}
                  type="checkbox"
                />
                <span>
                  <strong>{field.label}</strong>
                  <code>{field.field_id}</code>
                </span>
              </label>
              <div className="field-profile__meta">
                <span>{field.unit}</span>
                <span>{field.frequency}</span>
                <span>{field.coverage.estimated_coverage_pct}%</span>
              </div>
              {selected && (
                <label className="lag-control">
                  <span>{t("dataset.field.lagOverride")}</span>
                  <input
                    aria-label={`${field.label} ${t("dataset.field.lagOverride")}`}
                    min="0"
                    placeholder={String(field.recommended_lag_sessions)}
                    type="number"
                    value={lagOverrides[field.field_id] ?? ""}
                    onChange={(event) =>
                      onLagOverride(field.field_id, event.target.value)
                    }
                  />
                </label>
              )}
              <details className="field-profile__details">
                <summary>{t("dataset.field.details")}</summary>
                <dl>
                  <dt>{t("dataset.field.meaning")}</dt>
                  <dd>{field.description}</dd>
                  <dt>{t("dataset.field.disclosure")}</dt>
                  <dd>{field.disclosure_basis}</dd>
                  <dt>{t("dataset.field.availability")}</dt>
                  <dd>{field.available_date_basis}</dd>
                  <dt>{t("dataset.field.recommendedLag")}</dt>
                  <dd>{field.recommended_lag_sessions} sessions</dd>
                  <dt>{t("dataset.field.evidence")}</dt>
                  <dd>{field.evidence}</dd>
                </dl>
              </details>
            </article>
          );
        })}
      </div>
      {catalog.data && catalog.data.page_count > 1 && (
        <div
          className="pagination"
          aria-label={t("dataset.catalog.pagination")}
        >
          <Button disabled={page === 1} onClick={() => setPage(page - 1)}>
            {t("dataset.catalog.previous")}
          </Button>
          <span>
            {page} / {catalog.data.page_count}
          </span>
          <Button
            disabled={page === catalog.data.page_count}
            onClick={() => setPage(page + 1)}
          >
            {t("dataset.catalog.next")}
          </Button>
        </div>
      )}
    </section>
  );
};
