import {
  queryOptions,
  skipToken,
  useMutation,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import {
  strategyWorkbenchApi,
  type ChartLineBody,
  type ChartLineEditBody,
  type ChartLinePreviewBody,
  type ChartLinesBody,
  type CreateChartLineBody,
  type InstrumentKind,
  type Timeframe,
} from "../../../shared/api";

/** 차트 대상 하나(종목 티커 또는 지수 코드). 이름은 응답 머리(`context.instrument`)가 갖는다. */
export type ChartTarget = { kind: InstrumentKind; code: string };

export const chartKey = () => ["stock-chart"] as const;

/** 선 변이(만들기·고치기·지우기)의 묶음 키. 저장 상태 줄이 진행 중인 변이를 이 키로 센다. */
export const CHART_LINE_MUTATION_KEY = [...chartKey(), "line-mutation"] as const;

const linesKey = (target: ChartTarget | null) =>
  [...chartKey(), "lines", target?.kind ?? null, target?.code ?? null] as const;

/** 종목·지수 검색. 빈 검색어는 묻지 않는다(서버도 빈 결과다). */
export const chartInstrumentSearchQuery = (query: string) =>
  queryOptions({
    queryKey: [...chartKey(), "search", query.trim()],
    queryFn: () => strategyWorkbenchApi.searchChartInstruments(query.trim()),
    enabled: query.trim() !== "",
    staleTime: 60_000,
  });

/**
 * 한 주기의 캔들 전부. 표시 기간은 화면이 이 계열에서 자른다(`visibleRange`) — 기준일(`as_of`)에서 거꾸로 세는
 * 기간 버튼과 좌표 입력의 날짜 목록이 같은 계열을 읽는다. 대상을 고르기 전(null)에는 묻지 않는다.
 */
export const chartCandlesQuery = (target: ChartTarget | null, timeframe: Timeframe) =>
  queryOptions({
    queryKey: [...chartKey(), "candles", target?.kind ?? null, target?.code ?? null, timeframe],
    queryFn:
      target === null
        ? skipToken
        : () =>
            strategyWorkbenchApi.getChartCandles({
              kind: target.kind,
              code: target.code,
              timeframe,
            }),
  });

/** 그 대상의 저장 선 전부(주기와 무관). 레벨 선을 그릴 점은 서버가 계산해 싣는다. */
export const chartLinesQuery = (target: ChartTarget | null) =>
  queryOptions({
    queryKey: linesKey(target),
    queryFn:
      target === null ? skipToken : () => strategyWorkbenchApi.listChartLines(target.kind, target.code),
  });

/**
 * 저장 전 미리보기(상태 없는 POST라 같은 초안은 같은 응답이다). 화면은 가격을 계산하지 않고 이 응답의 두 점을
 * 잇는다. 거절(진행 중 봉·순서)은 다시 물어도 같으므로 재시도하지 않는다. 초안이 없으면(null) 묻지 않는다.
 */
export const chartLinePreviewQuery = (draft: ChartLinePreviewBody | null) =>
  queryOptions({
    queryKey: [...chartKey(), "preview", draft],
    queryFn: draft === null ? skipToken : () => strategyWorkbenchApi.previewChartLine(draft),
    staleTime: Infinity,
    gcTime: 30_000,
    retry: false,
  });

const targetOf = (line: Pick<ChartLineBody, "instrument_kind" | "instrument_code">) => ({
  kind: line.instrument_kind,
  code: line.instrument_code,
});

const refreshLines = (queryClient: QueryClient, target: ChartTarget) =>
  queryClient.invalidateQueries({ queryKey: linesKey(target) });

export const useCreateChartLine = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: CHART_LINE_MUTATION_KEY,
    mutationFn: (body: CreateChartLineBody) => strategyWorkbenchApi.createChartLine(body),
    onSuccess: (line) => refreshLines(queryClient, targetOf(line)),
  });
};

/** PUT은 칸 전체를 바꾼다 — 본문은 `chartLineEdit`로 지금 칸을 모두 실어 만든다. */
export const useUpdateChartLine = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: CHART_LINE_MUTATION_KEY,
    mutationFn: ({ lineId, body }: { lineId: string; body: ChartLineEditBody }) =>
      strategyWorkbenchApi.updateChartLine(lineId, body),
    onSuccess: (line) => {
      // 응답이 고친 선 전부(그릴 점 포함)라 목록을 다시 읽지 않고 그 줄만 바꾼다.
      queryClient.setQueryData<ChartLinesBody>(linesKey(targetOf(line)), (current) =>
        current === undefined
          ? current
          : {
              ...current,
              lines: current.lines.map((item) =>
                item.line_id === line.line_id ? line : item,
              ),
            },
      );
    },
  });
};

export const useDeleteChartLine = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: CHART_LINE_MUTATION_KEY,
    mutationFn: (line: ChartLineBody) => strategyWorkbenchApi.deleteChartLine(line.line_id),
    onSuccess: (_, line) => refreshLines(queryClient, targetOf(line)),
  });
};
