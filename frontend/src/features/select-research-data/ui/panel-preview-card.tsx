import {
  presentResearchCell,
  useResearchPanelPreview,
} from "../../../entities/dataset";
import type { DataStep } from "../../../entities/strategy";
import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import type { LagOverrides } from "../model/research-data-selection";

type PanelPreviewCardProps = {
  dataStep: DataStep;
  securityIds: string[];
  selectedFieldIds: string[];
  lagOverrides: LagOverrides;
};

export const PanelPreviewCard = ({
  dataStep,
  securityIds,
  selectedFieldIds,
  lagOverrides,
}: PanelPreviewCardProps) => {
  const panelPreview = useResearchPanelPreview();
  const preview = panelPreview.data;
  const canPreview = selectedFieldIds.length > 0 && securityIds.length > 0;

  const requestPreview = async (confirmedWarningIds: string[] = []) => {
    await panelPreview.mutateAsync({
      query: {
        start: dataStep.start,
        end: dataStep.end,
        security_ids: securityIds,
        field_ids: selectedFieldIds,
        lag_overrides: Object.entries(lagOverrides)
          .filter(([, sessions]) => sessions !== "")
          .map(([fieldId, sessions]) => ({
            field_id: fieldId,
            sessions: Number(sessions),
          })),
      },
      venue: "XKRX",
      row_limit: 24,
      column_limit: 6,
      confirmed_warning_ids: confirmedWarningIds,
    });
  };

  return (
    <section className="data-card data-card--preview">
      <div className="data-card__title">
        <span>03</span>
        <div>
          <h3>{t("dataset.preview.title")}</h3>
          <p>{t("dataset.preview.description")}</p>
        </div>
      </div>
      <Button
        disabled={!canPreview || panelPreview.isPending}
        onClick={() => void requestPreview()}
        tone="primary"
      >
        {t("dataset.preview.run")}
      </Button>
      {!canPreview && (
        <p className="inline-state">{t("dataset.preview.requirements")}</p>
      )}
      {preview && (
        <>
          <dl className="cost-summary">
            <div>
              <dt>{t("dataset.preview.estimatedCells")}</dt>
              <dd>{preview.cost.estimated_cells.toLocaleString()}</dd>
            </div>
            <div>
              <dt>{t("dataset.preview.estimatedBytes")}</dt>
              <dd>{Math.ceil(preview.cost.estimated_bytes / 1024)} KB</dd>
            </div>
            <div>
              <dt>{t("dataset.preview.returned")}</dt>
              <dd>
                {preview.cost.returned_rows} × {preview.cost.returned_columns}
              </dd>
            </div>
          </dl>
          {preview.truncated && (
            <p className="preview-note">{t("dataset.preview.truncated")}</p>
          )}
          {preview.warnings.length > 0 && (
            <aside
              className="data-warning"
              aria-label={t("dataset.warning.title")}
            >
              <strong>{t("dataset.warning.title")}</strong>
              <ul>
                {preview.warnings.map((warning) => (
                  <li key={warning.warning_id}>{warning.message}</li>
                ))}
              </ul>
              {preview.confirmation_required && (
                <Button
                  onClick={() =>
                    void requestPreview(
                      preview.warnings
                        .filter((warning) => warning.requires_confirmation)
                        .map((warning) => warning.warning_id),
                    )
                  }
                  tone="primary"
                >
                  {t("dataset.warning.confirm")}
                </Button>
              )}
            </aside>
          )}
          {!preview.confirmation_required && preview.panel.cells.length > 0 && (
            <div className="panel-table-wrap">
              <table className="panel-table">
                <thead>
                  <tr>
                    <th>{t("dataset.preview.asOf")}</th>
                    <th>{t("dataset.preview.security")}</th>
                    <th>{t("dataset.preview.field")}</th>
                    <th>{t("dataset.preview.effectiveDate")}</th>
                    <th>{t("dataset.preview.availableDate")}</th>
                    <th>{t("dataset.preview.value")}</th>
                    <th>{t("dataset.preview.state")}</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.panel.cells.map((cell) => {
                    const presentation = presentResearchCell(cell);
                    return (
                      <tr
                        key={`${cell.as_of}-${cell.security_id}-${cell.field_id}`}
                      >
                        <td>{cell.as_of}</td>
                        <td>{cell.security_id}</td>
                        <td>{cell.field_id}</td>
                        <td>{cell.source_effective_date}</td>
                        <td>{cell.available_date}</td>
                        <td>{presentation.displayValue}</td>
                        <td>
                          <span
                            className={`cell-kind cell-kind--${presentation.tone}`}
                          >
                            {t(presentation.labelKey)}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      {panelPreview.isError && (
        <p className="inline-state inline-state--error">
          {t("dataset.preview.error")}
        </p>
      )}
    </section>
  );
};
