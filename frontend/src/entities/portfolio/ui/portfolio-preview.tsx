import type { PortfolioPreview as PortfolioPreviewContract } from "../../../shared/api";
import { t } from "../../../shared/config";

const percentage = (value: number): string => `${(value * 100).toFixed(2)}%`;
const score = (value: number | null): string =>
  value === null ? "—" : value.toFixed(4);
const reasonLabel = (value: string): string => value.replaceAll("_", " ");

export const PortfolioPreview = ({
  preview,
}: {
  preview: PortfolioPreviewContract;
}) => {
  const frame =
    preview.tape.frames
      .slice()
      .reverse()
      .find((item) => item.targets.length > 0) ?? preview.tape.frames.at(-1);
  const gross =
    frame?.targets.reduce((sum, target) => sum + Math.abs(target.weight), 0) ??
    0;
  const net =
    frame?.targets.reduce((sum, target) => sum + target.weight, 0) ?? 0;

  return (
    <section className="portfolio-preview" aria-live="polite">
      <header className="portfolio-preview__header">
        <div>
          <span className="section-kicker">IMMUTABLE TARGET TAPE</span>
          <h3>{t("portfolio.preview.title")}</h3>
        </div>
        <span
          className={`engine-badge engine-badge--${preview.engine.compatible ? "ready" : "blocked"}`}
        >
          {preview.engine.compatible
            ? t("portfolio.engine.ready")
            : t("portfolio.engine.blocked")}
        </span>
      </header>

      <dl className="target-summary">
        <div>
          <dt>{t("portfolio.preview.frames")}</dt>
          <dd>{preview.tape.frames.length}</dd>
        </div>
        <div>
          <dt>Gross</dt>
          <dd>{percentage(gross)}</dd>
        </div>
        <div>
          <dt>Net</dt>
          <dd>{percentage(net)}</dd>
        </div>
        <div>
          <dt>{t("portfolio.preview.targets")}</dt>
          <dd>{frame?.targets.length ?? 0}</dd>
        </div>
      </dl>

      <div className="target-timing">
        <span>
          T close · {t("portfolio.preview.signal")}{" "}
          <strong>{frame?.signal_as_of ?? "—"}</strong>
        </span>
        <span aria-hidden="true">→</span>
        <span>
          T+1 open · {t("portfolio.preview.execution")}{" "}
          <strong>{frame?.execution_on ?? "—"}</strong>
        </span>
      </div>

      <div className="target-contract">
        <code title={preview.tape.tape_hash}>
          tape {preview.tape.tape_hash.slice(0, 12)}…
        </code>
        <span>
          {preview.engine.requirements.schedule} ·{" "}
          {preview.engine.requirements.actions.join(", ")}
        </span>
        {preview.engine.requirements.features.map((feature) => (
          <span className="requirement-chip" key={feature}>
            {feature}
          </span>
        ))}
      </div>

      {frame === undefined ? (
        <p className="inline-state">{t("portfolio.preview.empty")}</p>
      ) : (
        <div className="candidate-table-wrap">
          <table className="candidate-table">
            <thead>
              <tr>
                <th>{t("portfolio.preview.security")}</th>
                <th>{t("portfolio.preview.score")}</th>
                <th>{t("portfolio.preview.rank")}</th>
                <th>{t("portfolio.preview.side")}</th>
                <th>{t("portfolio.preview.weight")}</th>
                <th>{t("portfolio.preview.decision")}</th>
              </tr>
            </thead>
            <tbody>
              {frame.candidates.map((candidate) => (
                <tr key={candidate.security_id}>
                  <td>
                    <code>{candidate.security_id}</code>
                  </td>
                  <td>{score(candidate.composite_score)}</td>
                  <td>{candidate.rank ?? "—"}</td>
                  <td>{candidate.side ?? "—"}</td>
                  <td>{percentage(candidate.target_weight)}</td>
                  <td>
                    {candidate.exclusion_reasons.length === 0 ? (
                      <span className="decision-chip decision-chip--selected">
                        {t("portfolio.preview.selected")}
                      </span>
                    ) : (
                      candidate.exclusion_reasons.map((reason) => (
                        <span className="decision-chip" key={reason}>
                          {reasonLabel(reason)}
                        </span>
                      ))
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
};
