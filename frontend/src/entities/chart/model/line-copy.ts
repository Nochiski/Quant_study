import {
  ApiRequestError,
  type ChartErrorDetail,
  type ChartLineBody,
  type ChosenBy,
  type InstrumentKind,
  type LineIssue,
  type LineKind,
  type RegimeVerdict,
  type Structure,
  type SwingChoice,
  type Timeframe,
  type TrendDirection,
  type WindowSource,
  type ZoneSide,
} from "../../../shared/api";
import { t, tCode, tFill, tOptional, type MessageKey } from "../../../shared/config";
import { formatPrice } from "./chart-price";

/** 개별종목차트 거절 코드(backend `ChartErrorDetail.code`). */
type ChartErrorCode = ChartErrorDetail["code"];

// 코드 유니온 전부에 문구 키가 있어야 이 함수가 typecheck를 지난다 — 코드가 늘면 문구를 요구한다.
const errorKey = (code: ChartErrorCode): MessageKey => `chart.error.${code}`;

/**
 * 차트 요청 실패의 본문 문장. 서버가 코드로 거절했으면 `chart.error.<code>`, 아니면 일반 문구다. 서버 원문은
 * 화면 본문에 쓰지 않는다 — `FailureNotice`의 접힌 상세(`failureReason`)로 간다.
 */
export const chartErrorSentence = (error: unknown): string => {
  const code = error instanceof ApiRequestError ? error.code : undefined;
  // 생성 SDK보다 새 코드가 올 수 있어 문구가 있는지는 실행 때 다시 본다(없으면 일반 문구).
  const sentence =
    code !== undefined && code.startsWith("chart.")
      ? tOptional(errorKey(code as ChartErrorCode))
      : null;
  return sentence ?? t("chart.error.generic");
};

export const lineKindLabel = (kind: LineKind): string => tCode(`chart.kind.${kind}`, kind);

export const timeframeLabel = (timeframe: Timeframe): string =>
  tCode(`chart.timeframe.${timeframe}`, timeframe);

export const lineIssueLabel = (issue: LineIssue): string => tCode(`chart.issue.${issue}`, issue);

// 분석 응답 어휘의 짧은 이름(라벨). 판정·문장 자체는 서버가 완성한다 — 여기서는 값의 이름만 고른다.
export const regimeVerdictLabel = (verdict: RegimeVerdict): string =>
  tCode(`chart.regime.verdict.${verdict}`, verdict);

export const structureLabel = (structure: Structure): string =>
  tCode(`chart.regime.structure.${structure}`, structure);

export const trendDirectionLabel = (direction: TrendDirection): string =>
  tCode(`chart.direction.${direction}`, direction);

export const chosenByLabel = (chosen: ChosenBy): string => tCode(`chart.chosenBy.${chosen}`, chosen);

export const windowSourceLabel = (source: WindowSource): string =>
  tCode(`chart.windowSource.${source}`, source);

export const swingChoiceLabel = (swing: SwingChoice): string => tCode(`chart.swing.${swing}`, swing);

export const zoneSideLabel = (side: ZoneSide): string => tCode(`chart.zoneSide.${side}`, side);

/**
 * 목록·카드에서 선을 부르는 이름. 수평선은 가격, 되돌림·채널은 기준점 날짜로 구분한다. 다른 주기로 만든 선이면
 * 그 주기를 붙인다(그 주기 화면에서 고친다).
 */
export const lineTitle = (
  line: Pick<ChartLineBody, "kind" | "anchors" | "timeframe">,
  instrumentKind: InstrumentKind,
  viewTimeframe: Timeframe,
): string => {
  const [first, second] = line.anchors;
  const body =
    line.kind === "hline"
      ? tFill("chart.line.title.hline", {
          kind: lineKindLabel(line.kind),
          price: line.anchors.map((anchor) => formatPrice(anchor.price, instrumentKind)).join(" ~ "),
        })
      : line.kind === "fib_retracement"
        ? tFill("chart.line.title.retracement", {
            kind: lineKindLabel(line.kind),
            from: first?.bar_date ?? "",
            to: second?.bar_date ?? "",
          })
        : tFill("chart.line.title.channel", {
            kind: lineKindLabel(line.kind),
            from: first?.bar_date ?? "",
          });
  return line.timeframe === viewTimeframe
    ? body
    : tFill("chart.line.title.otherTimeframe", { title: body, timeframe: timeframeLabel(line.timeframe) });
};
