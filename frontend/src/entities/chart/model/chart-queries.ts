import {
  queryOptions,
  skipToken,
  useMutation,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import {
  strategyWorkbenchApi,
  type ChartDrawingBody,
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

/** 선 변이(만들기·고치기·지우기)의 묶음 키. 저장 상태 줄이 진행 중인 변이를 이 키(앞부분 일치)로 센다. */
export const CHART_LINE_MUTATION_KEY = [...chartKey(), "line-mutation"] as const;
const UPDATE_LINE_MUTATION_KEY = [...CHART_LINE_MUTATION_KEY, "update"] as const;

/**
 * 선 변이는 한 줄로 차례대로 보낸다(TanStack `scope`). 고치기는 칸 전체를 바꾸는 PUT이라, 겹쳐 보내면 뒤
 * 요청이 앞 요청의 옛 칸으로 앞 수정을 덮어 지운다(메모를 입력하고 바로 잠금을 누르는 경우). 차례대로 보내면
 * 다음 요청의 본문은 앞 응답이 캐시에 쓴 최신 선에서 만든다.
 */
const LINE_MUTATION_SCOPE = { id: "stock-chart-lines" };

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
 *
 * `gesture`는 지금 그리기·끌기 동작 하나의 이름이다. 다음 응답을 기다리는 동안 앞 응답을 그대로 보이되(깜박임
 * 방지) 같은 동작 안에서만 그런다 — 다른 선을 그리기 시작하면 앞 선의 잔상을 보이지 않는다.
 */
export const chartLinePreviewQuery = (draft: ChartLinePreviewBody | null, gesture: string) =>
  queryOptions({
    queryKey: [...chartKey(), "preview", gesture, draft],
    queryFn: draft === null ? skipToken : () => strategyWorkbenchApi.previewChartLine(draft),
    staleTime: Infinity,
    gcTime: 30_000,
    retry: false,
    placeholderData: (previous: ChartDrawingBody | undefined, previousQuery) =>
      previousQuery?.queryKey[2] === gesture ? previous : undefined,
  });

const targetOf = (line: Pick<ChartLineBody, "instrument_kind" | "instrument_code">) => ({
  kind: line.instrument_kind,
  code: line.instrument_code,
});

const refreshLines = (queryClient: QueryClient, target: ChartTarget) =>
  queryClient.invalidateQueries({ queryKey: linesKey(target) });

/**
 * 저장 선 고치기 본문. `PUT /lines/{id}`는 칸 전체를 바꾸는 계약이라(빠진 칸은 기본값으로 돌아간다) 지금 선의
 * 칸을 모두 싣고 바꿀 칸만 덮는다. 레벨이 null(종류별 기본)이면 null 그대로 보낸다 — 기본 레벨을 화면이 펼쳐
 * 저장하지 않는다.
 */
export const chartLineEdit = (
  line: ChartLineBody,
  patch: Partial<ChartLineEditBody> = {},
): ChartLineEditBody => ({
  anchors: line.anchors,
  extend_right: line.extend_right,
  memo: line.memo,
  locked: line.locked,
  visible: line.visible,
  levels: line.levels,
  ...patch,
});

/**
 * 고칠 칸. 함수면 보낼 때의 최신 선을 받아 칸을 만든다 — 키보드로 연달아 옮길 때 앞 이동이 끝난 가격에서 다음
 * 호가를 센다. null을 돌려주면 보내지 않는다.
 */
export type ChartLinePatch =
  | Partial<ChartLineEditBody>
  | ((latest: ChartLineBody) => Partial<ChartLineEditBody> | null);

export const useCreateChartLine = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: CHART_LINE_MUTATION_KEY,
    scope: LINE_MUTATION_SCOPE,
    mutationFn: (body: CreateChartLineBody) => strategyWorkbenchApi.createChartLine(body),
    onSuccess: (line) => refreshLines(queryClient, targetOf(line)),
  });
};

type LineEditVariables = { line: ChartLineBody; patch: ChartLinePatch };

// 응답을 기다리지 않고 바로 보이는 칸(그릴 점과 무관한 값). 기준점·레벨은 그릴 점을 서버가 다시 계산하므로
// 응답이 올 때까지 앞 모양 그대로 둔다.
const instantFields = (patch: ChartLinePatch): Partial<ChartLineBody> => {
  if (typeof patch === "function") return {};
  const { memo, locked, visible, extend_right } = patch;
  return Object.fromEntries(
    Object.entries({ memo, locked, visible, extend_right }).filter(([, value]) => value !== undefined),
  );
};

const replaceLine = (
  queryClient: QueryClient,
  lineId: string,
  target: ChartTarget,
  change: (line: ChartLineBody) => ChartLineBody,
) =>
  queryClient.setQueryData<ChartLinesBody>(linesKey(target), (current) =>
    current === undefined
      ? current
      : { ...current, lines: current.lines.map((item) => (item.line_id === lineId ? change(item) : item)) },
  );

export const useUpdateChartLine = () => {
  const queryClient = useQueryClient();
  // 아직 차례를 기다리는 같은 선 고치기의 바로 보이는 칸 — 앞 응답이 캐시를 바꿔도 다시 얹어 체크 칸이 되돌아
  // 깜박이지 않게 한다.
  const pendingInstant = (lineId: string, except: LineEditVariables) =>
    queryClient
      .getMutationCache()
      .findAll({ mutationKey: UPDATE_LINE_MUTATION_KEY, status: "pending" })
      .map((mutation) => mutation.state.variables as LineEditVariables | undefined)
      .filter(
        (variables): variables is LineEditVariables =>
          variables !== undefined && variables !== except && variables.line.line_id === lineId,
      )
      .reduce<Partial<ChartLineBody>>((fields, variables) => ({ ...fields, ...instantFields(variables.patch) }), {});
  return useMutation({
    mutationKey: UPDATE_LINE_MUTATION_KEY,
    scope: LINE_MUTATION_SCOPE,
    // 체크 칸·메모처럼 바로 보이는 칸은 누르는 순간 캐시에 얹는다(응답·차례를 기다리는 동안 칸이 그대로면
    // 다시 누르게 된다). 본문은 아래에서 차례가 왔을 때의 최신 선으로 다시 만든다.
    onMutate: ({ line, patch }: LineEditVariables) =>
      replaceLine(queryClient, line.line_id, targetOf(line), (item) => ({ ...item, ...instantFields(patch) })),
    mutationFn: async ({ line, patch }: LineEditVariables) => {
      // 차례가 왔을 때의 최신 선(앞 고치기 응답이 쓴 캐시)에서 본문을 만든다
      const latest =
        queryClient
          .getQueryData<ChartLinesBody>(linesKey(targetOf(line)))
          ?.lines.find((item) => item.line_id === line.line_id) ?? line;
      const fields = typeof patch === "function" ? patch(latest) : patch;
      if (fields === null) return latest;
      return strategyWorkbenchApi.updateChartLine(line.line_id, chartLineEdit(latest, fields));
    },
    onSuccess: (line, variables) => {
      // 응답이 고친 선 전부(그릴 점 포함)라 목록을 다시 읽지 않고 그 줄만 바꾼다.
      const waiting = pendingInstant(line.line_id, variables);
      replaceLine(queryClient, line.line_id, targetOf(line), () => ({ ...line, ...waiting }));
    },
    onError: (_, { line }) => refreshLines(queryClient, targetOf(line)),
  });
};

export const useDeleteChartLine = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: CHART_LINE_MUTATION_KEY,
    scope: LINE_MUTATION_SCOPE,
    mutationFn: (line: ChartLineBody) => strategyWorkbenchApi.deleteChartLine(line.line_id),
    onSuccess: (_, line) => refreshLines(queryClient, targetOf(line)),
  });
};
