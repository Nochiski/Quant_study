import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import type { FactorDefinition } from "../../../shared/api";

type FactorCardProps = {
  factor: FactorDefinition;
  selected: boolean;
  onAdd: (factor: FactorDefinition) => void;
};

export const FactorCard = ({ factor, selected, onAdd }: FactorCardProps) => {
  const available =
    factor.availability === "implemented" && factor.default_graph !== null;
  return (
    <article className="factor-catalog-card">
      <div className="factor-catalog-card__heading">
        <span className={`factor-category factor-category--${factor.category}`}>
          {factor.category}
        </span>
        <span className="factor-preference">{factor.preference} preferred</span>
      </div>
      <strong>{factor.label}</strong>
      <code>{factor.factor_id}</code>
      <p>{factor.description}</p>
      <dl>
        <div>
          <dt>{t("factor.card.unit")}</dt>
          <dd>{factor.output_unit}</dd>
        </div>
        <div>
          <dt>{t("factor.card.history")}</dt>
          <dd>{factor.minimum_history_sessions} sessions</dd>
        </div>
      </dl>
      <div className="factor-field-tags">
        {factor.required_field_ids.map((fieldId) => (
          <code key={fieldId}>{fieldId}</code>
        ))}
      </div>
      <Button disabled={!available || selected} onClick={() => onAdd(factor)}>
        {selected
          ? t("factor.catalog.selected")
          : available
            ? t("factor.catalog.add")
            : t("factor.catalog.unavailable")}
      </Button>
    </article>
  );
};
