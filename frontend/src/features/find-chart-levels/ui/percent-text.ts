import { t, tFill } from "../../../shared/config";

/** 현재가 대비 비율 한 칸: "16.5% 위"·"3.2% 아래". 값이 없으면 "—". */
export const percentText = (change: number | null): string =>
  change === null
    ? "—"
    : Math.abs(change) < 0.05
      ? t("chart.finder.atCurrent")
      : tFill(change > 0 ? "chart.finder.above" : "chart.finder.below", {
          pct: Math.abs(change).toFixed(1),
        });
