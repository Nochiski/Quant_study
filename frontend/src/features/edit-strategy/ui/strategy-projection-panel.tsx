import { t } from "../../../shared/config";
import { Badge } from "../../../shared/ui";
import type { FieldApplicability } from "../model/field-applicability";
import type { StrategyProjection } from "../model/strategy-projection";
import "./strategy-projection-panel.css";

type StrategyProjectionPanelProps = {
  projection: StrategyProjection;
  view: "json" | "form";
  /** pointer별 조건표 판정(P2-03). 컴파일된 spec은 기본값이 채워져 있어 판정이 확정된다. */
  applicability?: ReadonlyMap<string, FieldApplicability>;
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
  applicability,
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
    // schema 1.1: 컴파일된 spec은 모든 단계를 채워 보내지만 계약상 선택 필드이므로 빈 단계도 그대로 그린다.
    { id: "data", values: projection.spec.data },
    { id: "portfolio", values: projection.spec.portfolio ?? {} },
    { id: "risk", values: projection.spec.risk ?? {} },
    { id: "execution", values: projection.spec.execution ?? {} },
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
                {Object.entries(section.values).map(([field, value]) => {
                  const pointer =
                    section.id === "metadata"
                      ? `/${field}`
                      : `/${section.id}/${field}`;
                  const inapplicable =
                    applicability?.get(pointer)?.applicable === false;
                  return (
                    <div key={field}>
                      <dt>
                        <code>{field}</code>
                      </dt>
                      <dd>
                        <code>{valueText(value)}</code>
                        {inapplicable ? (
                          <>
                            {" "}
                            <Badge tone="warn">
                              {t("contract.applicable.badge")}
                            </Badge>
                          </>
                        ) : null}
                      </dd>
                    </div>
                  );
                })}
              </dl>
            </section>
          ))}
        </div>
      )}
    </section>
  );
};
