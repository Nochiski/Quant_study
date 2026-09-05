import type { DiffEntry } from "../../../shared/api";
import { t } from "../../../shared/config";
import "./semantic-diff-table.css";

const renderValue = (value: unknown): string =>
  value === undefined ? "—" : JSON.stringify(value);

const DIFF_KIND_MESSAGE = {
  added: "diff.kind.added",
  removed: "diff.kind.removed",
  changed: "diff.kind.changed",
} as const;

export const SemanticDiffTable = ({ entries }: { entries: DiffEntry[] }) =>
  entries.length === 0 ? (
    <p className="semantic-diff__empty">{t("diff.semantic.empty")}</p>
  ) : (
    <div className="semantic-diff__scroll">
      <table className="semantic-diff__table">
        <thead>
          <tr>
            <th scope="col">{t("diff.column.pointer")}</th>
            <th scope="col">{t("diff.column.kind")}</th>
            <th scope="col">{t("diff.column.before")}</th>
            <th scope="col">{t("diff.column.after")}</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, index) => (
            <tr key={`${entry.kind}:${entry.pointer}:${index}`}>
              <td>
                <code>{entry.pointer || "/"}</code>
              </td>
              <td>{t(DIFF_KIND_MESSAGE[entry.kind])}</td>
              <td>
                <code>{renderValue(entry.before)}</code>
              </td>
              <td>
                <code>{renderValue(entry.after)}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
