import { t } from "../../../shared/config";
import { Badge } from "../../../shared/ui";
import type { StrategyProjection } from "../model/strategy-projection";
import "./strategy-projection-panel.css";

type StrategyProjectionPanelProps = {
  projection: StrategyProjection;
  view: "json";
};

/** backend canonical StrategySpec JSON(읽기 전용). Form view는 P4-04부터 `StrategyFormPanel`이 맡는다. */
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
      <pre className="strategy-projection__json">
        <code>{projection.canonicalJson}</code>
      </pre>
    </section>
  );
};
