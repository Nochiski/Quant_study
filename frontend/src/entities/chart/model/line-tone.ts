import type { ChartLineBody, LineKind } from "../../../shared/api";

/** 내 선(손으로 그었거나 자동 후보를 복사한 선)은 잉크, 저장한 자동 후보는 종류 색이다(설계 문서 2장 색 역할). */
export type LineTone = "mine" | `auto-${LineKind}`;

/** 저장 선의 색 역할 판정 하나 — 선 그림과 오른쪽 축 배지가 함께 쓴다. */
export const lineTone = (line: Pick<ChartLineBody, "source" | "kind">): LineTone =>
  line.source === "auto" ? `auto-${line.kind}` : "mine";
