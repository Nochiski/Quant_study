import { lineTone, type ChartLineBody } from "../../../entities/chart";

/**
 * 자동 분석 후보 칸의 줄: 만든 것마다 한 줄. 채널·되돌림은 선 하나가 한 줄이고, 수평 구간은 한 번에 만든 띠 묶음(같은
 * 주기·같은 자동 기록 — 판정 구간과 파라미터가 같으면 같은 찾기의 결과다)이 한 줄이며 펼쳐서 구간마다 켜고 끈다.
 * 자동 선인지는 색 역할 판정(`lineTone`) 하나로 가른다 — 내 선(손으로 그었거나 복사한 선)은 "내가 그은 선" 칸이다.
 */
export type AutoRow = {
  key: string;
  kind: ChartLineBody["kind"];
  lines: ChartLineBody[];
};

export const autoRows = (lines: readonly ChartLineBody[]): AutoRow[] => {
  const rows: AutoRow[] = [];
  const zones = new Map<string, AutoRow>();
  for (const line of lines) {
    if (lineTone(line) === "mine") continue;
    if (line.kind !== "hline") {
      rows.push({ key: line.line_id, kind: line.kind, lines: [line] });
      continue;
    }
    const key = `zones|${line.timeframe}|${JSON.stringify(line.auto)}`;
    const row = zones.get(key);
    if (row !== undefined) row.lines.push(line);
    else {
      const created: AutoRow = { key, kind: line.kind, lines: [line] };
      zones.set(key, created);
      rows.push(created);
    }
  }
  return rows;
};
