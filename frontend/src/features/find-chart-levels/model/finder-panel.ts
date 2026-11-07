import type {
  DateRange,
  LineKind,
  SwingChoice,
  TrendDirection,
} from "../../../entities/chart";

/**
 * 자동 찾기 패널(생성 패널 3종·근거·중첩)의 화면 상태와 상태 바꾸기 규칙의 owner. 서버 응답(판정·후보)은 담지 않고
 * 요청에 실을 입력(구간·방향·첫 점·파라미터)과 고른 후보 자리만 둔다(`.claude/rules/frontend-api-state.md`).
 *
 * 패널은 레이어 칸 자리를 바꾸는 비모달 패널이다. 닫으면 레이어 칸으로 돌아가며 연 버튼의 이름(`returnKey`)을
 * 남긴다 — 레이어 칸이 다시 그려진 뒤 그 버튼으로 초점을 돌려준다.
 */

/** 채널 방향 고르기: 추천을 따름(서버 판정이 정한다), 직접 고른 방향, 그리지 않음. */
export type DirectionChoice = "recommended" | TrendDirection | "none";

/**
 * 생성 패널의 입력. 값이 null이면 서버 기본값이다(민감도·묶는 폭·최소 접촉은 응답 `record.params`가 실제 값을 보인다).
 * `window`가 null이면 채널은 서버 기본 판정 구간(가운데 제안 칩)이다.
 */
export type FinderDraft = {
  window: DateRange | null;
  candidateSensitivity: number | null;
  structureSensitivity: number | null;
  /** 채널 */
  direction: DirectionChoice;
  firstAnchor: string | null;
  /** 되돌림 */
  swing: SwingChoice;
  levels: readonly number[] | null;
  extendRight: boolean;
  /** 수평 구간 */
  widthAtr: number | null;
  minTouches: number | null;
  /** 고른 채널 후보 자리(순위순 index). */
  candidate: number;
  /** 수평 구간 결과 중 뺀 구간의 자리(기본은 모두 추가). */
  excludedZones: readonly number[];
};

export const DEFAULT_DRAFT: FinderDraft = {
  window: null,
  candidateSensitivity: null,
  structureSensitivity: null,
  direction: "recommended",
  firstAnchor: null,
  swing: "range_extremes",
  levels: null,
  extendRight: true,
  widthAtr: null,
  minTouches: null,
  candidate: 0,
  excludedZones: [],
};

/** 생성 패널을 여는 도구: 수평 구간(`hline` 띠), 되돌림, 채널. */
export type FinderTool = LineKind;

export type FinderPanel =
  | { view: "layers"; focusKey: string | null }
  | { view: "create"; tool: FinderTool; draft: FinderDraft; returnKey: string }
  | { view: "evidence"; lineIds: readonly string[]; returnKey: string }
  | { view: "confluence"; returnKey: string };

export const INITIAL_PANEL: FinderPanel = { view: "layers", focusKey: null };

/** 입력 중 고칠 수 있는 칸(고른 후보 자리 제외). */
export type DraftInput = Partial<
  Omit<FinderDraft, "candidate" | "excludedZones">
>;

export type FinderAction =
  | {
      type: "openCreate";
      tool: FinderTool;
      seed?: Partial<FinderDraft>;
      returnKey: string;
    }
  | { type: "openEvidence"; lineIds: readonly string[]; returnKey: string }
  | { type: "openConfluence"; returnKey: string }
  | { type: "close" }
  /** 다른 종목·주기로 넘어갔다 — 구간 날짜가 그 계열의 봉이라 패널을 닫고 초점을 옮기지 않는다. */
  | { type: "reset" }
  | { type: "edit"; input: DraftInput }
  | { type: "pickCandidate"; index: number }
  | { type: "toggleZone"; index: number };

/**
 * 입력이 바뀌면 결과(후보 순위·찾은 구간)가 새로 오므로 고른 후보는 1순위로, 뺀 구간은 비운다. 판정 구간을 바꾸면 첫
 * 점도 비운다 — 첫 점은 그 구간 안의 확정 피벗 목록에서 고른 것이라 다른 구간에서는 `outside_window`가 된다.
 */
const edited = (draft: FinderDraft, input: DraftInput): FinderDraft => ({
  ...draft,
  ...input,
  firstAnchor:
    input.firstAnchor !== undefined
      ? input.firstAnchor
      : input.window !== undefined || input.direction !== undefined
        ? null
        : draft.firstAnchor,
  candidate: 0,
  excludedZones: [],
});

export const finderReducer = (
  panel: FinderPanel,
  action: FinderAction,
): FinderPanel => {
  switch (action.type) {
    case "openCreate":
      return {
        view: "create",
        tool: action.tool,
        draft: {
          ...DEFAULT_DRAFT,
          ...action.seed,
          candidate: 0,
          excludedZones: [],
        },
        returnKey: action.returnKey,
      };
    case "openEvidence":
      return {
        view: "evidence",
        lineIds: action.lineIds,
        returnKey: action.returnKey,
      };
    case "openConfluence":
      return { view: "confluence", returnKey: action.returnKey };
    case "close":
      return panel.view === "layers"
        ? panel
        : { view: "layers", focusKey: panel.returnKey };
    case "reset":
      return INITIAL_PANEL;
    case "edit":
      return panel.view === "create"
        ? { ...panel, draft: edited(panel.draft, action.input) }
        : panel;
    case "pickCandidate":
      return panel.view === "create"
        ? { ...panel, draft: { ...panel.draft, candidate: action.index } }
        : panel;
    case "toggleZone": {
      if (panel.view !== "create") return panel;
      const excluded = panel.draft.excludedZones;
      return {
        ...panel,
        draft: {
          ...panel.draft,
          excludedZones: excluded.includes(action.index)
            ? excluded.filter((index) => index !== action.index)
            : [...excluded, action.index],
        },
      };
    }
  }
};
