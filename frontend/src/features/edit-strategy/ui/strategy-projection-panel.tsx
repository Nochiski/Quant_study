import { t } from "../../../shared/config";
import { Badge } from "../../../shared/ui";
import type { StrategyProjection } from "../model/strategy-projection";
import "./strategy-projection-panel.css";

type StrategyProjectionPanelProps = {
  projection: StrategyProjection;
  view: "json" | "form";
};

const valueText = (value: unknown): string => {
  if (value === undefined) return "—";
  const serialized = JSON.stringify(value);
  return serialized === undefined ? String(value) : serialized;
};

/** Backend-owned canonical StrategySpec rendered without any edit or reserialization path. */
export const StrategyProjectionPanel = ({
  projection,
  view,
}: StrategyProjectionPanelProps) => {
  if (projection.status === "unavailable") {
    return (
      <p className="strategy-projection__state" role="status">
        {t("projection.unavailable")}
      </p>
    );
  }

  const sections = [
    {
      id: "metadata",
      values: {
        title: projection.spec.title,
        description: projection.spec.description,
      },
    },
    { id: "data", values: projection.spec.data },
    { id: "portfolio", values: projection.spec.portfolio },
    { id: "risk", values: projection.spec.risk },
    { id: "execution", values: projection.spec.execution },
  ] as const;

  return (
    <section
      className="strategy-projection"
      aria-label={t(`projection.${view}.label`)}
    >
      <header className="strategy-projection__header">
        <div>
          <strong>{t(`projection.${view}.label`)}</strong>
          <span>{t("projection.readOnly")}</span>
        </div>
        <Badge tone={projection.stale ? "warn" : "ok"}>
          {projection.stale
            ? t("projection.staleBadge")
            : t("projection.currentBadge")}
        </Badge>
      </header>
      {projection.stale ? (
        <p className="strategy-projection__notice" role="status">
          {t("projection.stale")}
        </p>
      ) : null}
      <dl className="strategy-projection__provenance">
        <div>
          <dt>{t("projection.schemaVersion")}</dt>
          <dd>{projection.schemaVersion}</dd>
        </div>
        <div>
          <dt>{t("projection.specHash")}</dt>
          <dd>
            <code title={projection.specHash}>
              {projection.specHash.slice(0, 12)}…
            </code>
          </dd>
        </div>
      </dl>
      {view === "json" ? (
        <pre className="strategy-projection__json">
          <code>{projection.canonicalJson}</code>
        </pre>
      ) : (
        <div className="strategy-projection__form">
          {sections.map((section) => (
            <section key={section.id}>
              <h2>{t(`projection.section.${section.id}`)}</h2>
              <dl>
                {Object.entries(section.values).map(([field, value]) => (
                  <div key={field}>
                    <dt>
                      <code>{field}</code>
                    </dt>
                    <dd>
                      <code>{valueText(value)}</code>
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      )}
    </section>
  );
};
