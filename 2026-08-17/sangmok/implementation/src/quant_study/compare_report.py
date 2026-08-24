"""엔진 대조 결과를 자체완결 HTML 리포트로 렌더링한다.

외부 asset 없이 인라인 JSON + 인라인 SVG 스크립트만 사용하므로
파일 하나를 브라우저로 열면 바로 보인다 (tests/manual 용 수동 도구).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from quant_study.engine_diff import EquityDiffReport


@dataclass(frozen=True)
class ScenarioComparison:
    scenario: str
    engine_curve: dict[date, float]
    zipline_curve: dict[date, float]
    report: EquityDiffReport


def to_json_payload(
    comparisons: list[ScenarioComparison], params: dict[str, object]
) -> dict[str, object]:
    def curve(points: dict[date, float]) -> list[list[object]]:
        return [[session.isoformat(), equity] for session, equity in sorted(points.items())]

    scenarios: list[dict[str, object]] = []
    for comparison in comparisons:
        report = comparison.report
        scenarios.append(
            {
                "scenario": comparison.scenario,
                "ok": report.ok,
                "matched_sessions": report.matched_sessions,
                "left_only_sessions": report.left_only_sessions,
                "right_only_sessions": report.right_only_sessions,
                "max_rel_diff": report.max_rel_diff,
                "max_diff_session": (
                    report.max_diff_session.isoformat() if report.max_diff_session else None
                ),
                "mean_rel_diff": report.mean_rel_diff,
                "breach_count": report.breach_count,
                "rel_tol": report.rel_tol,
                "engine_curve": curve(comparison.engine_curve),
                "zipline_curve": curve(comparison.zipline_curve),
            }
        )
    return {"params": params, "scenarios": scenarios}


_PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>엔진 대조 리포트</title>
<style>
  body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 24px auto;
         max-width: 1080px; color: #1a1c1f; background: #f7f8fa; }
  h1 { font-size: 24px; } h2 { font-size: 18px; margin: 28px 0 8px; }
  .params, .stats { border-collapse: collapse; font-size: 13px; }
  .params td, .stats td, .stats th { border: 1px solid #d8dde4; padding: 4px 10px; }
  .stats th { background: #eef1f5; text-align: left; }
  .badge { border-radius: 6px; color: #fff; font-size: 12px; font-weight: 700;
           padding: 2px 8px; vertical-align: middle; }
  .ok { background: #0f766e; } .bad { background: #b42318; }
  .chart { background: #fff; border: 1px solid #d8dde4; border-radius: 8px;
           margin: 8px 0 4px; }
  .legend { color: #555; font-size: 12px; margin-bottom: 16px; }
  .engine { color: #0f766e; } .zipline { color: #2563eb; } .diff { color: #b42318; }
</style>
</head>
<body>
<h1>커스텀 엔진 vs Zipline 대조 리포트</h1>
<table class="params" id="params"></table>
<div id="scenarios"></div>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const data = JSON.parse(document.getElementById("payload").textContent);

const paramsTable = document.getElementById("params");
for (const [key, value] of Object.entries(data.params)) {
  const row = paramsTable.insertRow();
  row.insertCell().textContent = key;
  row.insertCell().textContent = String(value);
}

function svgLine(series, x0, x1, y0, y1, width, height, pad) {
  const sx = v => pad + (v - x0) / Math.max(x1 - x0, 1) * (width - 2 * pad);
  const sy = v => height - pad - (v - y0) / Math.max(y1 - y0, 1e-12) * (height - 2 * pad);
  return series
    .map(([x, y], i) => (i ? "L" : "M") + sx(x).toFixed(1) + " " + sy(y).toFixed(1))
    .join(" ");
}

function drawChart(container, seriesList, title, formatY) {
  const width = 1040, height = 260, pad = 46;
  const xs = seriesList.flatMap(s => s.points.map(p => p[0]));
  const ys = seriesList.flatMap(s => s.points.map(p => p[1]));
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y0 = Math.min(...ys), y1 = Math.max(...ys);
  let svg = `<svg class="chart" width="${width}" height="${height}"
    role="img" aria-label="${title}">`;
  svg += `<text x="${pad}" y="18" font-size="13" font-weight="700">${title}</text>`;
  const fmtDate = ms => new Date(ms).toISOString().slice(0, 10);
  svg += `<text x="${pad}" y="${height - pad + 16}" font-size="11">${fmtDate(x0)}</text>`;
  svg += `<text x="${width - pad}" y="${height - pad + 16}" font-size="11"
    text-anchor="end">${fmtDate(x1)}</text>`;
  svg += `<text x="4" y="${height - pad}" font-size="11">${formatY(y0)}</text>`;
  svg += `<text x="4" y="${pad}" font-size="11">${formatY(y1)}</text>`;
  for (const s of seriesList) {
    const path = svgLine(s.points, x0, x1, y0, y1, width, height, pad);
    svg += `<path d="${path}" fill="none" stroke="${s.color}"
      stroke-width="${s.widthPx}" stroke-opacity="${s.opacity}"/>`;
  }
  svg += "</svg>";
  container.insertAdjacentHTML("beforeend", svg);
}

const root = document.getElementById("scenarios");
for (const s of data.scenarios) {
  const section = document.createElement("section");
  const badge = s.ok
    ? '<span class="badge ok">OK</span>'
    : '<span class="badge bad">MISMATCH</span>';
  section.innerHTML = `<h2>${s.scenario} ${badge}</h2>
    <table class="stats"><tr>
      <th>공통 세션</th><th>한쪽에만</th><th>max rel diff</th><th>mean rel diff</th>
      <th>허용치</th><th>초과 세션</th></tr><tr>
      <td>${s.matched_sessions}</td>
      <td>engine ${s.left_only_sessions} / zipline ${s.right_only_sessions}</td>
      <td>${s.max_rel_diff.toExponential(2)} (${s.max_diff_session ?? "-"})</td>
      <td>${s.mean_rel_diff.toExponential(2)}</td>
      <td>${s.rel_tol.toExponential(0)}</td>
      <td>${s.breach_count}</td></tr></table>`;
  root.appendChild(section);

  const toXY = curve => curve.map(([d, v]) => [Date.parse(d), v]);
  const engine = toXY(s.engine_curve), zipline = toXY(s.zipline_curve);
  drawChart(section, [
    { points: zipline, color: "#2563eb", widthPx: 3.2, opacity: 0.45 },
    { points: engine, color: "#0f766e", widthPx: 1.4, opacity: 1.0 },
  ], "Equity curve (원)", v => Math.round(v).toLocaleString());

  const ziplineByDate = new Map(s.zipline_curve.map(([d, v]) => [d, v]));
  const relDiff = s.engine_curve
    .filter(([d]) => ziplineByDate.has(d))
    .map(([d, v]) => {
      const z = ziplineByDate.get(d);
      return [Date.parse(d), Math.abs(v - z) / Math.max(Math.abs(z), 1)];
    });
  drawChart(section, [{ points: relDiff, color: "#b42318", widthPx: 1.2, opacity: 1.0 }],
    "상대 오차 |engine - zipline| / zipline", v => v.toExponential(1));

  section.insertAdjacentHTML("beforeend",
    '<div class="legend"><span class="engine">■ custom engine</span> · ' +
    '<span class="zipline">■ zipline</span> · <span class="diff">■ rel diff</span></div>');
}
</script>
</body>
</html>
"""


def render_html(comparisons: list[ScenarioComparison], params: dict[str, object]) -> str:
    payload = json.dumps(to_json_payload(comparisons, params), ensure_ascii=False)
    # </script> 조기 종료 방지
    payload = payload.replace("</", "<\\/")
    return _PAGE.replace("__PAYLOAD__", payload)
