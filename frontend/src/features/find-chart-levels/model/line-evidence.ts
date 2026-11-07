import {
  timeframeLabel,
  trendDirectionLabel,
  type ChartAutoRecordBody,
  type ChartLineBody,
  type SwingChoice,
  type Timeframe,
  type TrendDirection,
} from "../../../entities/chart";
import { t, tFill, type MessageKey } from "../../../shared/config";
import type { DirectionChoice, FinderDraft } from "./finder-panel";

/**
 * 저장한 자동 선(자동 후보 칸의 한 줄 — 채널·되돌림은 선 하나, 수평 구간은 함께 만든 띠 묶음) → 근거 패널·후보 칸 표시
 * 모델. 가격·레벨 가격·생긴 날은 응답 값 그대로이고(설계 문서 6장), 여기서는 줄을 고르고 이름을 붙이고 현재가 대비
 * 비율만 센다. 자동 기록(`auto.params`)의 칸 이름은 backend `AutoParams.to_params`가 정한 wire 계약이다.
 */

export type EvidenceAnchor = { role: MessageKey; barDate: string; price: number };

/** 이번 주기(마지막 완성 봉) 레벨 가격 한 줄. 수평 띠는 레벨이 없다(null). */
export type EvidenceLevel = { level: number | null; price: number; barDate: string; changePct: number | null };

export type EvidenceZone = { lineId: string; low: number; high: number; formed: string | null };

export type EvidenceParam = { key: string; value: string };

export type LineEvidence = {
  kind: ChartLineBody["kind"];
  timeframe: ChartLineBody["timeframe"];
  /** 채널 방향(기록 `params.direction`). 채널이 아니면 null. */
  direction: TrendDirection | null;
  anchors: EvidenceAnchor[];
  levels: EvidenceLevel[];
  zones: EvidenceZone[];
  /** 생긴 날 — 응답 `created`(서버가 저장한 입력으로 다시 계산). 다시 계산할 수 없으면 null. */
  created: string | null;
  record: ChartAutoRecordBody | null;
  params: EvidenceParam[];
};

const DIRECTIONS: readonly TrendDirection[] = ["up", "down"];
const SWINGS: readonly SwingChoice[] = ["range_extremes", "latest_major", "latest_confirmed"];

const stringParam = <T extends string>(record: ChartAutoRecordBody | null, key: string, allowed: readonly T[]) => {
  const value = record?.params[key];
  return allowed.find((item) => item === value) ?? null;
};

/** 자동 기록 `params`의 숫자 칸 하나(서버가 쓴 값). 없거나 숫자가 아니면 null. */
export const serverParam = (params: ChartAutoRecordBody["params"] | undefined, key: string): number | null => {
  const value = params?.[key];
  return typeof value === "number" ? value : null;
};

/** 현재가 대비 비율(%). 현재가가 없거나 0 이하면 null. */
export const changeFromCurrent = (price: number, current: number | null): number | null =>
  current === null || current <= 0 ? null : (price / current - 1) * 100;

/** 판정 강도 S 표기: 부호를 붙인 소수 둘째 자리(−1.75, +4.11). 값이 없으면 "—". */
export const strengthText = (value: number | null): string =>
  value === null ? "—" : `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(2)}`;

/** 수평 띠(기준점 둘)의 하단·상단 — 기준점 순서와 무관하게. */
export const zoneBounds = (line: Pick<ChartLineBody, "anchors">): { low: number; high: number } => {
  const prices = line.anchors.map((anchor) => anchor.price);
  return { low: Math.min(...prices), high: Math.max(...prices) };
};

const channelDirection = (line: ChartLineBody): TrendDirection | null =>
  line.kind === "fib_channel" ? stringParam(line.auto ?? null, "direction", DIRECTIONS) : null;

/**
 * 자동 후보 한 줄(근거 패널 제목 포함)의 이름: 수평 구간 "· N곳", 채널은 방향, 되돌림은 종류. 다른 주기로 만든 줄이면
 * 그 주기를 붙인다.
 */
export const autoLineTitle = (lines: readonly ChartLineBody[], viewTimeframe: Timeframe): string => {
  const [first] = lines;
  const direction = channelDirection(first);
  const body =
    first.kind === "hline"
      ? tFill("chart.finder.zones.row", { count: lines.length })
      : direction === null
        ? t(`chart.finder.kind.${first.kind}`)
        : trendDirectionLabel(direction);
  return first.timeframe === viewTimeframe ? body : `${body} · ${timeframeLabel(first.timeframe)}`;
};

const anchorRoles = (line: ChartLineBody, direction: TrendDirection | null): MessageKey[] => {
  if (line.kind === "fib_channel")
    return direction === "down"
      ? ["chart.evidence.role.channel.down.0", "chart.evidence.role.channel.down.1", "chart.evidence.role.channel.down.2"]
      : ["chart.evidence.role.channel.up.0", "chart.evidence.role.channel.up.1", "chart.evidence.role.channel.up.2"];
  if (line.kind === "fib_retracement") return ["chart.anchor.fib_retracement.0", "chart.anchor.fib_retracement.1"];
  return ["chart.evidence.role.zone.low", "chart.evidence.role.zone.high"];
};

/**
 * 근거 표시 모델. `lines`는 한 줄의 선들(첫 선이 대표 — 묶음은 같은 기록을 공유한다). `current`는 현재가(응답 머리
 * `context.current_price`).
 */
export const lineEvidence = (lines: readonly ChartLineBody[], current: number | null): LineEvidence => {
  const [first] = lines;
  const record = first.auto ?? null;
  const direction = channelDirection(first);
  const roles = anchorRoles(first, direction);
  const zone = first.kind === "hline";
  return {
    kind: first.kind,
    timeframe: first.timeframe,
    direction,
    anchors: zone
      ? []
      : first.anchors.map((anchor, index) => ({ role: roles[index], barDate: anchor.bar_date, price: anchor.price })),
    levels: zone
      ? []
      : first.paths.map((path) => ({
          level: path.level,
          price: path.end.price,
          barDate: path.end.bar_date,
          changePct: changeFromCurrent(path.end.price, current),
        })),
    zones: zone ? lines.map((line) => ({ lineId: line.line_id, ...zoneBounds(line), formed: line.created })) : [],
    created: zone ? null : first.created,
    record,
    params: Object.entries(record?.params ?? {})
      .filter(([, value]) => value !== null)
      .map(([key, value]) => ({ key, value: String(value) })),
  };
};

/**
 * "조건 바꿔 다시 만들기": 저장한 자동 기록(판정 구간·민감도·도구 파라미터)과 레벨·연장으로 생성 패널 입력을 채운다.
 * 채널 방향은 저장한 방향을 직접 고른 것으로 연다 — 같은 방향을 다시 보고 조건만 바꾸려는 것이라서다. 채운 값은 저장
 * 기록 그대로라 "바꾼 값"으로 치지 않는다(`paramsTouched`는 패널을 열 때 거짓이다).
 */
export const recreateSeed = (line: ChartLineBody): Partial<FinderDraft> => {
  const record = line.auto ?? null;
  const start = record?.regime_window_start ?? null;
  const end = record?.regime_window_end ?? null;
  const direction = stringParam(record, "direction", DIRECTIONS);
  const firstAnchor = record?.params.first_anchor;
  return {
    window: start !== null && end !== null ? { start, end } : null,
    candidateSensitivity: serverParam(record?.params, "candidate_sensitivity"),
    structureSensitivity: serverParam(record?.params, "structure_sensitivity"),
    direction: (direction ?? "recommended") satisfies DirectionChoice,
    firstAnchor: typeof firstAnchor === "string" ? firstAnchor : null,
    swing: stringParam(record, "swing", SWINGS) ?? "range_extremes",
    levels: line.levels,
    extendRight: line.extend_right,
    widthAtr: serverParam(record?.params, "width_atr"),
    minTouches: serverParam(record?.params, "min_touches"),
  };
};
