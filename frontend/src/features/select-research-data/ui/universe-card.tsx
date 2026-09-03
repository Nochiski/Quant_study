import type { UniversePreview } from "../../../entities/dataset";
import type { DataStep } from "../../../entities/strategy";
import { t } from "../../../shared/config";

type UniverseCardProps = {
  value: DataStep;
  preview: UniversePreview | undefined;
  pending: boolean;
  onChange: (next: DataStep) => void;
};

export const UniverseCard = ({
  value,
  preview,
  pending,
  onChange,
}: UniverseCardProps) => (
  <section className="data-card">
    <div className="data-card__title">
      <span>01</span>
      <div>
        <h3>{t("dataset.universe.title")}</h3>
        <p>{t("dataset.universe.description")}</p>
      </div>
    </div>
    <div className="period-grid">
      <label>
        <span>{t("dataset.period.start")}</span>
        <input
          aria-label={t("dataset.period.start")}
          type="date"
          value={value.start}
          onChange={(event) =>
            onChange({ ...value, start: event.target.value })
          }
        />
      </label>
      <label>
        <span>{t("dataset.period.end")}</span>
        <input
          aria-label={t("dataset.period.end")}
          type="date"
          value={value.end}
          onChange={(event) => onChange({ ...value, end: event.target.value })}
        />
      </label>
      <label>
        <span>{t("dataset.universe.id")}</span>
        <input
          aria-label={t("dataset.universe.id")}
          value={value.universe_id}
          onChange={(event) =>
            onChange({ ...value, universe_id: event.target.value })
          }
        />
      </label>
    </div>
    {pending && <p className="inline-state">{t("dataset.universe.loading")}</p>}
    {preview && (
      <dl className="coverage-summary">
        <div>
          <dt>{t("dataset.universe.sessions")}</dt>
          <dd>{preview.coverage.session_count}</dd>
        </div>
        <div>
          <dt>{t("dataset.universe.members")}</dt>
          <dd>
            {preview.coverage.minimum_members}–
            {preview.coverage.maximum_members}
          </dd>
        </div>
        <div>
          <dt>{t("dataset.universe.gaps")}</dt>
          <dd>{preview.coverage.gap_session_count}</dd>
        </div>
      </dl>
    )}
  </section>
);
