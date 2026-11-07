import type {
  ChartConfluenceBody,
  ChartInstrumentBody,
  ChartLineBody,
  ChartTarget,
  Timeframe,
} from "../../../entities/chart";
import type { LevelFinder } from "../model/use-level-finder";
import { ConfluencePanel } from "./confluence-panel";
import { CreateChannelPanel } from "./create-channel-panel";
import { CreateRetracementPanel } from "./create-retracement-panel";
import { CreateZonesPanel } from "./create-zones-panel";
import { LineEvidencePanel } from "./line-evidence-panel";
import "./find-chart-levels.css";

type FinderPanelsProps = {
  finder: LevelFinder;
  target: ChartTarget;
  timeframe: Timeframe;
  instrument: ChartInstrumentBody;
  current: number | null;
  lines: readonly ChartLineBody[];
  levelChoices: readonly number[];
  confluence: ChartConfluenceBody | undefined;
  confluenceError: unknown;
  includeMine: boolean;
  onIncludeMine: (include: boolean) => void;
};

/** 열린 자동 찾기 패널 하나(레이어 칸 자리에 그린다). 레이어 칸 상태면 아무것도 그리지 않는다. */
export const FinderPanels = ({
  finder,
  target,
  timeframe,
  instrument,
  current,
  lines,
  levelChoices,
  confluence,
  confluenceError,
  includeMine,
  onIncludeMine,
}: FinderPanelsProps) => {
  const { panel } = finder;
  if (panel.view === "create") {
    if (panel.tool === "fib_channel")
      return (
        <CreateChannelPanel
          finder={finder}
          target={target}
          timeframe={timeframe}
          instrument={instrument}
        />
      );
    if (panel.tool === "fib_retracement")
      return (
        <CreateRetracementPanel
          finder={finder}
          target={target}
          timeframe={timeframe}
          instrument={instrument}
          current={current}
          levelChoices={levelChoices}
          confluence={confluence}
        />
      );
    return (
      <CreateZonesPanel
        finder={finder}
        target={target}
        timeframe={timeframe}
        instrument={instrument}
      />
    );
  }
  if (panel.view === "evidence") {
    const shown = panel.lineIds
      .map((lineId) => lines.find((line) => line.line_id === lineId))
      .filter((line): line is ChartLineBody => line !== undefined);
    return (
      <LineEvidencePanel
        finder={finder}
        lines={shown}
        target={target}
        timeframe={timeframe}
        instrument={instrument}
        current={current}
      />
    );
  }
  if (panel.view === "confluence")
    return (
      <ConfluencePanel
        finder={finder}
        confluence={confluence}
        error={confluenceError}
        includeMine={includeMine}
        onIncludeMine={onIncludeMine}
        lines={lines}
        instrument={instrument}
        timeframe={timeframe}
        current={current}
      />
    );
  return null;
};
