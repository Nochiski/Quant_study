import type { ReactNode } from "react";

import { t, tOptional } from "../../../shared/config";
import {
  formatContractValue,
  projectContractInspector,
  type ContractBound,
  type ContractCatalogProjection,
  type ContractInspectorSource,
} from "../model/contract-inspector";
import "./contract-inspector.css";

type ContractInspectorProps = {
  source: ContractInspectorSource;
  selectedPointer: string | undefined;
  /** Current parse tree, or the same-document last valid tree used by the outline. */
  tree: unknown;
  stale: boolean;
};

const EMPTY = "—";

const yesNo = (value: boolean): string =>
  value ? t("contract.yes") : t("contract.no");

const shortHash = (value: string): ReactNode => (
  <code title={value}>{value.length > 14 ? `${value.slice(0, 12)}…` : value}</code>
);

const codeValue = (value: unknown): ReactNode => (
  <code>{formatContractValue(value) ?? EMPTY}</code>
);

const boundText = (
  minimum: ContractBound | null,
  maximum: ContractBound | null,
): string =>
  [
    minimum
      ? `${minimum.inclusive ? "≥" : ">"} ${minimum.value}`
      : null,
    maximum
      ? `${maximum.inclusive ? "≤" : "<"} ${maximum.value}`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");

const Rows = ({ rows }: { rows: readonly [string, ReactNode][] }) => (
  <dl className="contract-inspector__rows">
    {rows.map(([label, value]) => (
      <div key={label}>
        <dt>{label}</dt>
        <dd>{value}</dd>
      </div>
    ))}
  </dl>
);

const statusMessage = (
  status: Exclude<ContractCatalogProjection, { status: "ready" }>["status"],
): string => {
  switch (status) {
    case "loading":
      return t("contract.catalog.loading");
    case "error":
      return t("contract.catalog.error");
    case "mismatch":
      return t("contract.catalog.mismatch");
    case "unselected":
      return t("contract.catalog.unselected");
    case "not-loaded":
      return t("contract.catalog.notLoaded");
    case "not-found":
      return t("contract.catalog.notFound");
    case "unsupported":
      return t("contract.catalog.unsupported");
  }
};

const CatalogDetails = ({
  catalog,
}: {
  catalog: ContractCatalogProjection;
}) => {
  if (catalog.kind === "other") {
    return (
      <section className="contract-inspector__section">
        <h3>{t("assist.source")}</h3>
        <Rows
          rows={[
            [t("assist.source"), <code key="namespace">{catalog.namespace}</code>],
            [
              t("ide.inspector.storedValue"),
              catalog.id ? <code key="id">{catalog.id}</code> : EMPTY,
            ],
          ]}
        />
        <p className="contract-inspector__notice">{statusMessage(catalog.status)}</p>
      </section>
    );
  }

  const versionRows: readonly [string, ReactNode][] = [
    [t("contract.expectedVersion"), <code key="expected">{catalog.expectedVersion}</code>],
    [
      t("contract.actualVersion"),
      catalog.actualVersion ? <code key="actual">{catalog.actualVersion}</code> : EMPTY,
    ],
  ];
  if (catalog.status !== "ready") {
    return (
      <section className="contract-inspector__section">
        <h3>
          {catalog.kind === "equity-field"
            ? t("contract.fieldDetails")
            : t("contract.factorDetails")}
        </h3>
        <Rows rows={versionRows} />
        <p
          className={`contract-inspector__notice${catalog.status === "mismatch" ? " contract-inspector__notice--warn" : ""}`}
          role={catalog.status === "error" || catalog.status === "mismatch" ? "alert" : undefined}
        >
          {statusMessage(catalog.status)}
        </p>
      </section>
    );
  }

  if (catalog.kind === "equity-field") {
    const { field, snapshot } = catalog;
    const revisions = snapshot.dataset_revisions
      .map((revision) => `${revision.dataset_id}@${revision.revision}`)
      .join(", ");
    return (
      <section className="contract-inspector__section">
        <h3>{t("contract.fieldDetails")}</h3>
        <div className="contract-inspector__catalog-card">
          <strong>{field.label}</strong>
          <code>{field.field_id}</code>
          <p>{field.description}</p>
        </div>
        <Rows
          rows={[
            ...versionRows,
            [t("dataset.catalog.dataset"), <code key="dataset">{field.dataset_id}</code>],
            [t("contract.frequency"), field.frequency],
            [t("contract.valueType"), field.value_type],
            [t("ide.inspector.unit"), field.unit],
            [t("contract.pointInTime"), yesNo(field.coverage.point_in_time)],
            [t("dataset.field.availability"), field.available_date_basis],
            [t("dataset.field.disclosure"), field.disclosure_basis],
            [
              t("dataset.field.recommendedLag"),
              `${field.recommended_lag_sessions} ${t("contract.sessions")}`,
            ],
            [
              t("contract.coverage"),
              `${field.coverage.estimated_coverage_pct}%`,
            ],
            [
              t("contract.window"),
              `${field.coverage.starts_on} → ${field.coverage.ends_on}`,
            ],
            [t("contract.venues"), field.coverage.venues.join(", ") || EMPTY],
            [
              t("contract.cellKinds"),
              field.coverage.supported_cell_kinds.join(", ") || EMPTY,
            ],
            [t("dataset.field.evidence"), field.evidence],
            [t("contract.snapshot"), <code key="snapshot">{snapshot.snapshot_id}</code>],
            [t("contract.source"), snapshot.source],
            [t("contract.builtAt"), snapshot.built_at],
            [t("contract.datasetRevisions"), revisions || EMPTY],
          ]}
        />
      </section>
    );
  }

  const { factor } = catalog;
  return (
    <section className="contract-inspector__section">
      <h3>{t("contract.factorDetails")}</h3>
      <div className="contract-inspector__catalog-card">
        <strong>{factor.label}</strong>
        <code>{factor.factor_id}</code>
        <p>{factor.description}</p>
      </div>
      <Rows
        rows={[
          ...versionRows,
          [t("contract.category"), factor.category],
          [t("contract.availability"), factor.availability],
          [t("contract.outputUnit"), factor.output_unit],
          [t("contract.preference"), factor.preference],
          [t("contract.missingPolicy"), factor.missing_policy],
          [
            t("contract.minimumHistory"),
            `${factor.minimum_history_sessions} ${t("contract.sessions")}`,
          ],
          [
            t("contract.requiredFields"),
            factor.required_field_ids.join(", ") || EMPTY,
          ],
          [t("contract.tags"), factor.tags?.join(", ") || EMPTY],
        ]}
      />
    </section>
  );
};

/** Backend-owned contract projection for the URL-owned selected JSON Pointer. */
export const ContractInspector = ({
  source,
  selectedPointer,
  tree,
  stale,
}: ContractInspectorProps) => {
  const projection = projectContractInspector(
    source,
    selectedPointer,
    tree,
    stale,
  );
  if (projection.status === "loading")
    return (
      <p className="contract-inspector__state" role="status">
        {t("contract.loading")}
      </p>
    );
  if (projection.status === "unavailable")
    return (
      <p className="contract-inspector__state contract-inspector__state--error" role="alert">
        {t("contract.unavailable")}
      </p>
    );
  if (projection.status === "incompatible")
    return (
      <div className="contract-inspector__state contract-inspector__state--error" role="alert">
        <p>{t("contract.incompatible")}</p>
        <Rows
          rows={[
            [
              t("contract.schemaVersion"),
              `${projection.schemaVersion} / ${projection.contractSchemaVersion}`,
            ],
            [
              t("contract.schemaHash"),
              <span key="hashes">
                {shortHash(projection.schemaHash)} / {shortHash(projection.contractSchemaHash)}
              </span>,
            ],
          ]}
        />
      </div>
    );
  if (projection.status === "unknown")
    return (
      <div className="contract-inspector__state" role="status">
        <code>{projection.pointer || t("contract.root")}</code>
        <p>{t("contract.unknown")}</p>
      </div>
    );

  const { field } = projection;
  const description = field.descriptionKey
    ? tOptional(field.descriptionKey)
    : null;
  const rows: [string, ReactNode][] = [
    [
      t("ide.inspector.path"),
      <code key="path">{field.pointer || t("contract.root")}</code>,
    ],
    [t("contract.templatePath"), <code key="template">{field.templatePointer || "/"}</code>],
    [
      t("assist.type"),
      <span key="type" className="contract-inspector__inline-values">
        <code>{field.type}</code>
        {field.nullable ? <span>{t("contract.nullable")}</span> : null}
        {field.required === null ? null : (
          <span>{field.required ? t("assist.required") : t("assist.optional")}</span>
        )}
      </span>,
    ],
  ];
  if (field.shape === "scalar") {
    rows.push([
      t("ide.inspector.storedValue"),
      field.value.present ? codeValue(field.value.raw) : t("contract.noValue"),
    ]);
    if (field.value.display !== null)
      rows.push([
        t("ide.inspector.displayValue"),
        <strong key="display">{field.value.display}</strong>,
      ]);
  }
  if (field.enumValues.length)
    rows.push([
      t("contract.enum"),
      <span key="enum" className="contract-inspector__inline-values">
        {field.enumValues.map((value) => <code key={value}>{value}</code>)}
      </span>,
    ]);
  if (field.hasConst)
    rows.push([t("contract.const"), codeValue(field.constValue)]);
  if (field.hasDefault)
    rows.push([t("ide.inspector.default"), codeValue(field.defaultValue)]);
  if (field.minimum || field.maximum)
    rows.push([
      t("contract.range"),
      <code key="range">{boundText(field.minimum, field.maximum)}</code>,
    ]);
  if (field.format) rows.push([t("contract.format"), <code key="format">{field.format}</code>]);
  if (field.unit)
    rows.push([
      t("ide.inspector.unit"),
      <span key="unit">
        <code>{field.unit}</code>
        {field.displayUnit ? ` → ${field.displayUnit}` : ""}
      </span>,
    ]);
  if (field.hasExample)
    rows.push([t("assist.example"), codeValue(field.example)]);
  if (field.appliedStage)
    rows.push([t("ide.inspector.stage"), <code key="stage">{field.appliedStage}</code>]);
  if (field.catalog)
    rows.push([t("assist.source"), <code key="catalog">{field.catalog}</code>]);
  if (field.reference)
    rows.push([t("assist.source"), <code key="reference">{field.reference}</code>]);

  return (
    <div className="contract-inspector">
      {projection.stale ? (
        <p className="contract-inspector__notice contract-inspector__notice--warn" role="status">
          {t("contract.stale")}
        </p>
      ) : null}
      <section className="contract-inspector__section">
        <h3>{field.shape === "root" ? t("contract.root") : field.templatePointer}</h3>
        <Rows rows={rows} />
        {field.shape !== "scalar" ? (
          <p className="contract-inspector__notice">{t("contract.noScalar")}</p>
        ) : null}
        {field.descriptionKey ? (
          <div className="contract-inspector__description">
            <strong>{t("contract.description")}</strong>
            <p>{description ?? field.descriptionKey}</p>
            <code title={t("contract.descriptionKey")}>{field.descriptionKey}</code>
          </div>
        ) : null}
      </section>

      {field.discriminator ? (
        <section className="contract-inspector__section">
          <h3>{t("contract.discriminator")}</h3>
          <Rows
            rows={[
              [
                t("ide.inspector.path"),
                <code key="owner">{field.discriminator.ownerPointer}</code>,
              ],
              [
                t("contract.discriminator"),
                <code key="property">{field.discriminator.propertyName}</code>,
              ],
              [
                t("contract.variants"),
                <span key="variants" className="contract-inspector__inline-values">
                  {field.discriminator.variants.map((variant) => (
                    <code key={variant}>{variant}</code>
                  ))}
                </span>,
              ],
              [
                t("contract.selectedBranch"),
                field.discriminator.selected ? (
                  <code key="selected">{field.discriminator.selected}</code>
                ) : (
                  EMPTY
                ),
              ],
            ]}
          />
        </section>
      ) : null}

      {projection.catalog ? <CatalogDetails catalog={projection.catalog} /> : null}

      <section className="contract-inspector__section">
        <h3>{t("contract.provenance")}</h3>
        <Rows
          rows={[
            [t("contract.schemaVersion"), <code key="version">{projection.provenance.schemaVersion}</code>],
            [t("contract.schemaHash"), shortHash(projection.provenance.schemaHash)],
            [t("contract.contractHash"), shortHash(projection.provenance.contractHash)],
          ]}
        />
      </section>
    </div>
  );
};
