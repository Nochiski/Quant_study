import type { ResearchPanelCell } from "../../../shared/api";

export type CellPresentation = {
  labelKey:
    | "dataset.cell.observed"
    | "dataset.cell.actualZero"
    | "dataset.cell.sourceOmittedZero"
    | "dataset.cell.missing"
    | "dataset.cell.notCollected"
    | "dataset.cell.coverageGap";
  tone: "value" | "zero" | "missing" | "not-collected" | "gap";
  displayValue: string;
};

export const presentResearchCell = (
  cell: ResearchPanelCell,
): CellPresentation => {
  if (cell.kind === "observed") {
    return {
      labelKey:
        cell.value === 0 ? "dataset.cell.actualZero" : "dataset.cell.observed",
      tone: cell.value === 0 ? "zero" : "value",
      displayValue:
        cell.value === null
          ? "—"
          : typeof cell.value === "number"
            ? new Intl.NumberFormat("ko-KR", {
                maximumFractionDigits: 2,
              }).format(cell.value)
            : String(cell.value),
    };
  }
  if (cell.kind === "source_omitted_zero") {
    return {
      labelKey: "dataset.cell.sourceOmittedZero",
      tone: "zero",
      displayValue: "0",
    };
  }
  if (cell.kind === "missing") {
    return {
      labelKey: "dataset.cell.missing",
      tone: "missing",
      displayValue: "—",
    };
  }
  if (cell.kind === "not_collected") {
    return {
      labelKey: "dataset.cell.notCollected",
      tone: "not-collected",
      displayValue: "—",
    };
  }
  return {
    labelKey: "dataset.cell.coverageGap",
    tone: "gap",
    displayValue: "—",
  };
};
