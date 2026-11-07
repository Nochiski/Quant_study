import { useQuery } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";

import {
  chartLineCreateBody,
  chartRegimeQuery,
  chosenByLabel,
  formatPrice,
  levelText,
  lineKindLabel,
  regimeVerdictLabel,
  structureLabel,
  timeframeLabel,
  useCreateChartLine,
  windowSourceLabel,
  type ChartInstrumentBody,
  type ChartLineBody,
  type ChartTarget,
  type Timeframe,
} from "../../../entities/chart";
import { t, tFill } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import { autoLineTitle, changeFromCurrent, lineEvidence, recreateSeed, strengthText } from "../model/line-evidence";
import type { LevelFinder } from "../model/use-level-finder";
import { FinderFailure } from "./finder-fields";
import { FinderPanelFrame } from "./finder-panel-frame";
import { percentText } from "./percent-text";

type LineEvidencePanelProps = {
  finder: LevelFinder;
  /** 근거를 볼 자동 선(자동 후보 칸의 한 줄 — 수평 구간은 묶음). */
  lines: readonly ChartLineBody[];
  target: ChartTarget;
  timeframe: Timeframe;
  instrument: ChartInstrumentBody;
  current: number | null;
};

/**
 * 자동 선의 근거: 기준점·생긴 날·판정(그때 = 저장 기록, 지금 = 같은 주기 기본 구간의 서버 판정)·이번 주기 레벨·
 * 알고리즘·읽을 때 주의·저장 구조, "내 선으로 복사"(`auto_copied` — 기준점을 고칠 수 있는 내 선)와 "조건 바꿔 다시
 * 만들기"(같은 구간·방향·파라미터로 생성 패널). 값은 모두 저장 선 응답과 판정 응답이다.
 */
export const LineEvidencePanel = ({ finder, lines, target, timeframe, instrument, current }: LineEvidencePanelProps) => {
  const id = useId();
  const { panel, dispatch } = finder;
  const create = useCreateChartLine();
  // 복사 연타 막기 — 부르는 순간 세운다(변이 상태는 다음 렌더에야 바뀐다). 단추는 끄지 않는다(초점을 잃는다).
  const copying = useRef(false);
  const [copied, setCopied] = useState<{ saved: number; total: number } | null>(null);
  const first = lines[0] as ChartLineBody | undefined;
  const isChannel = first?.kind === "fib_channel";
  const today = useQuery(
    chartRegimeQuery(
      isChannel && first !== undefined ? { kind: target.kind, code: target.code, timeframe: first.timeframe } : null,
    ),
  );
  if (panel.view !== "evidence" || first === undefined) return null;
  const close = () => dispatch({ type: "close" });
  const evidence = lineEvidence(lines, current);
  const record = evidence.record;
  const price = (value: number) => formatPrice(value, instrument.kind);
  const sameTimeframe = first.timeframe === timeframe;

  const copy = async () => {
    if (copying.current) return;
    copying.current = true;
    try {
      const results = await Promise.allSettled(
        lines.map((line) => create.mutateAsync(chartLineCreateBody(line, { source: "auto_copied" }))),
      );
      setCopied({ saved: results.filter((result) => result.status === "fulfilled").length, total: lines.length });
    } finally {
      copying.current = false;
    }
  };
  const stored = lines.map((line) => ({
    kind: line.kind,
    timeframe: line.timeframe,
    source: line.source,
    anchors: line.anchors,
    levels: line.levels,
    extend_right: line.extend_right,
    auto: line.auto,
  }));

  return (
    <FinderPanelFrame
      title={autoLineTitle(lines, timeframe)}
      subtitle={tFill("chart.finder.evidence.subtitle", { name: instrument.name, timeframe: timeframeLabel(first.timeframe) })}
      onClose={close}
      footer={
        <>
          <Button tone="primary" onClick={() => void copy()}>
            {t("chart.finder.evidence.copy")}
          </Button>
          <Button
            disabled={!sameTimeframe}
            onClick={() =>
              dispatch({ type: "openCreate", tool: first.kind, seed: recreateSeed(first), returnKey: panel.returnKey })
            }
          >
            {t("chart.finder.evidence.recreate")}
          </Button>
        </>
      }
    >
      {copied === null ? null : (
        <p className={copied.saved === copied.total ? "finder-note" : "finder-warning"} role="status">
          {copied.saved === copied.total
            ? t("chart.finder.evidence.copied")
            : tFill("chart.finder.partial", { failed: copied.total - copied.saved, total: copied.total })}
        </p>
      )}
      {create.error ? <FinderFailure error={create.error} /> : null}
      {sameTimeframe ? null : (
        <p className="finder-note">
          {tFill("chart.finder.evidence.otherTimeframe", { timeframe: timeframeLabel(first.timeframe) })}
        </p>
      )}

      {record?.regime ? (
        <section className="finder-section" aria-labelledby={`${id}-regime`}>
          <h3 id={`${id}-regime`}>{t("chart.finder.regime.title")}</h3>
          <dl className="finder-facts">
            <dt>{t("chart.finder.evidence.window")}</dt>
            <dd>
              {`${record.regime_window_start ?? "—"} ~ ${record.regime_window_end ?? "—"}`}
              {record.window_source ? ` · ${windowSourceLabel(record.window_source)}` : ""}
            </dd>
            <dt>{t("chart.finder.evidence.then")}</dt>
            <dd>
              {regimeVerdictLabel(record.regime.verdict)}
              {record.regime.strength === null ? "" : ` · S ${strengthText(record.regime.strength)}`}
              {` · ${structureLabel(record.regime.structure)}`}
              {record.chosen_by ? ` · ${chosenByLabel(record.chosen_by)}` : ""}
            </dd>
            <dt>{t("chart.finder.evidence.today")}</dt>
            <dd>
              {today.data
                ? `${tFill("chart.finder.evidence.todayWindow", { bars: today.data.window.bars })} ${regimeVerdictLabel(today.data.verdict)}${today.data.strength === null ? "" : ` · S ${strengthText(today.data.strength)}`}`
                : today.isError
                  ? t("chart.finder.error")
                  : t("chart.finder.loading")}
            </dd>
          </dl>
          {today.data ? <p className="finder-note">{today.data.sentence}</p> : null}
        </section>
      ) : null}

      {evidence.anchors.length > 0 ? (
        <section className="finder-section" aria-labelledby={`${id}-anchors`}>
          <h3 id={`${id}-anchors`}>{t("chart.finder.evidence.anchors")}</h3>
          <table className="finder-table">
            <thead>
              <tr>
                <th scope="col">{t("chart.finder.evidence.role")}</th>
                <th scope="col">{t("chart.anchor.date")}</th>
                <th scope="col">{t("chart.finder.price")}</th>
              </tr>
            </thead>
            <tbody>
              {evidence.anchors.map((anchor) => (
                <tr key={anchor.role}>
                  <th scope="row">{t(anchor.role)}</th>
                  <td>{anchor.barDate}</td>
                  <td>{price(anchor.price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="finder-note">
            {evidence.created === null
              ? t("chart.finder.evidence.noCreated")
              : tFill("chart.finder.evidence.created", { date: evidence.created })}
          </p>
        </section>
      ) : null}

      {evidence.zones.length > 0 ? (
        <section className="finder-section" aria-labelledby={`${id}-zones`}>
          <h3 id={`${id}-zones`}>{t("chart.finder.zones.found")}</h3>
          <table className="finder-table">
            <thead>
              <tr>
                <th scope="col">{t("chart.finder.zones.range")}</th>
                <th scope="col">{t("chart.finder.zones.formed")}</th>
                <th scope="col">{t("chart.finder.fromCurrent")}</th>
              </tr>
            </thead>
            <tbody>
              {evidence.zones.map((zone) => (
                <tr key={zone.lineId}>
                  <th scope="row">{`${price(zone.low)}–${price(zone.high)}`}</th>
                  <td>{zone.formed ?? "—"}</td>
                  <td>{percentText(changeFromCurrent((zone.low + zone.high) / 2, current))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {evidence.levels.length > 0 ? (
        <section className="finder-section" aria-labelledby={`${id}-levels`}>
          <h3 id={`${id}-levels`}>
            {t("chart.finder.evidence.levels")}
            <span className="finder-section__meta">
              {tFill("chart.finder.evidence.levelsAt", { date: evidence.levels[0].barDate })}
            </span>
          </h3>
          <table className="finder-table">
            <thead>
              <tr>
                <th scope="col">{t("chart.finder.level")}</th>
                <th scope="col">{t("chart.finder.price")}</th>
                <th scope="col">{t("chart.finder.fromCurrent")}</th>
              </tr>
            </thead>
            <tbody>
              {evidence.levels.map((level) => (
                <tr key={level.level ?? "h"}>
                  <th scope="row">{level.level === null ? "—" : levelText(level.level)}</th>
                  <td>{price(level.price)}</td>
                  <td>{percentText(level.changePct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {record ? (
        <section className="finder-section" aria-labelledby={`${id}-algorithm`}>
          <h3 id={`${id}-algorithm`}>{t("chart.finder.evidence.algorithm")}</h3>
          <p className="finder-note finder-mono">
            {`${record.algorithm_version} · ${lineKindLabel(first.kind)} · ${evidence.params.map((param) => `${param.key}=${param.value}`).join(", ")}`}
          </p>
        </section>
      ) : null}

      <section className="finder-section" aria-labelledby={`${id}-caution`}>
        <h3 id={`${id}-caution`}>{t("chart.finder.evidence.caution")}</h3>
        <ul className="finder-list">
          {isChannel ? <li>{t("chart.finder.evidence.caution.width")}</li> : null}
          <li>{t("chart.finder.evidence.caution.formed")}</li>
          <li>{t("chart.finder.evidence.caution.signal")}</li>
        </ul>
      </section>

      <details className="finder-section finder-details">
        <summary>{t("chart.finder.evidence.stored")}</summary>
        <pre className="finder-mono finder-pre">{JSON.stringify(stored.length === 1 ? stored[0] : stored, null, 2)}</pre>
        <p className="finder-note">{t("chart.finder.evidence.storedNote")}</p>
      </details>
    </FinderPanelFrame>
  );
};
