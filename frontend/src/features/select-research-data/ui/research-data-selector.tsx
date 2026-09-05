import { useMemo, useState } from "react";

import { useUniversePreview } from "../../../entities/dataset";
import type { DataStep } from "../../../entities/strategy";
import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import type { LagOverrides } from "../model/research-data-selection";
import { FieldCatalogCard } from "./field-catalog-card";
import { PanelPreviewCard } from "./panel-preview-card";
import { UniverseCard } from "./universe-card";

type ResearchDataSelectorProps = {
  value: DataStep;
  onChange: (next: DataStep) => void;
};

export const ResearchDataSelector = ({
  value,
  onChange,
}: ResearchDataSelectorProps) => {
  const [selectedFieldIds, setSelectedFieldIds] = useState<string[]>([
    "price.close",
  ]);
  const [lagOverrides, setLagOverrides] = useState<LagOverrides>({});
  const universe = useUniversePreview({
    venue: "XKRX",
    start: value.start,
    end: value.end,
  });
  const securityIds = useMemo(() => {
    const ids = new Set<string>();
    for (const point of universe.data?.universe.points ?? []) {
      for (const member of point.members) {
        ids.add(member.security_id);
      }
    }
    return [...ids];
  }, [universe.data]);

  return (
    <section
      className="data-workspace"
      aria-label={t("dataset.workspace.title")}
    >
      <header className="data-workspace__header">
        <div>
          <span className="section-kicker">
            {t("dataset.workspace.kicker")}
          </span>
          <h2>{t("dataset.workspace.title")}</h2>
          <p>{t("dataset.workspace.description")}</p>
        </div>
        <Button
          onClick={() =>
            onChange({ ...value, start: "2024-01-02", end: "2024-01-12" })
          }
        >
          {t("dataset.period.useSample")}
        </Button>
      </header>

      <div className="data-layout">
        <div className="data-column">
          <UniverseCard
            onChange={onChange}
            pending={universe.isPending}
            preview={universe.data}
            value={value}
          />
          <FieldCatalogCard
            lagOverrides={lagOverrides}
            onLagOverride={(fieldId, sessions) =>
              setLagOverrides((current) => ({
                ...current,
                [fieldId]: sessions,
              }))
            }
            onToggleField={(fieldId) =>
              setSelectedFieldIds((current) =>
                current.includes(fieldId)
                  ? current.filter((id) => id !== fieldId)
                  : [...current, fieldId],
              )
            }
            selectedFieldIds={selectedFieldIds}
          />
        </div>
        <PanelPreviewCard
          dataStep={value}
          lagOverrides={lagOverrides}
          securityIds={securityIds}
          selectedFieldIds={selectedFieldIds}
        />
      </div>
    </section>
  );
};
