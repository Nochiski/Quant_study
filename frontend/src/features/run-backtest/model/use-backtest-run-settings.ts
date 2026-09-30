import { useCallback, useMemo, useState } from "react";

import {
  runEnvironmentWireText,
  useRunEnvironmentSchema,
} from "../../../entities/backtest";
import type { RunEnvironment } from "../../../shared/api";
import { t } from "../../../shared/config";
import {
  initialRunEnvironmentValues,
  runEnvironmentFields,
  runEnvironmentWireRecord,
  validateRunEnvironment,
  type RunEnvironmentField,
  type RunEnvironmentValidation,
  type RunEnvironmentValues,
} from "./run-environment";
import {
  runFieldLabel,
  runSettingsBlockedReason,
  runSettingsProblems,
} from "./run-settings-problems";
import {
  buildBacktestRunOptions,
  DEFAULT_BACKTEST_RUN_SETTINGS,
  OOS_START_FIELD,
  type BacktestRunSettingsFields,
} from "./run-settings";

/**
 * 실행 설정의 마지막 사용값(spec D6: 전략별 local UI state). 서버는 run manifest 에만 기록한다.
 * 전략별 칸이 없으면 마지막으로 쓴 값을 쓴다 — 새 전략을 저장해 전략 id 가 생겨도 방금 고른 기간·
 * 유니버스가 이어진다. 저장소를 쓸 수 없으면(사생활 모드 등) 조용히 스키마 기본값으로 시작한다.
 */
export const RUN_ENVIRONMENT_STORAGE_PREFIX =
  "quant-workbench.run-environment.v2";
const LAST_USED = "last";
/**
 * v1 은 참여 기준 기본값이 `session_volume` 이던 때 건드리지 않은 칸까지 모두 저장했다. 그 옛 기본값이 새
 * 기본값 `adv20` 을 덮지 않게, v2 값이 없을 때 v1 을 읽되 참여 기준 칸은 버린다(#396 리뷰 P2-1). 스키마
 * 기본값을 바꾸는 PR 은 판본을 올리고 여기서 그 칸을 버린다.
 */
const LEGACY_STORAGE_PREFIX = "quant-workbench.run-environment.v1";
const LEGACY_DROPPED: readonly string[] = ["participation_basis"];

const storage = (): Storage | null => {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
};

const readRecord = (storageKey: string): RunEnvironmentValues | null => {
  try {
    const text = storage()?.getItem(storageKey);
    if (text === null || text === undefined) return null;
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed))
      return null;
    return Object.fromEntries(
      Object.entries(parsed).filter(
        (entry): entry is [string, string] => typeof entry[1] === "string",
      ),
    );
  } catch {
    return null;
  }
};

const readStored = (key: string): RunEnvironmentValues | null => {
  const current = readRecord(`${RUN_ENVIRONMENT_STORAGE_PREFIX}:${key}`);
  if (current !== null) return current;
  const legacy = readRecord(`${LEGACY_STORAGE_PREFIX}:${key}`);
  return legacy === null
    ? null
    : Object.fromEntries(
        Object.entries(legacy).filter(
          ([name]) => !LEGACY_DROPPED.includes(name),
        ),
      );
};

const writeStored = (key: string, values: RunEnvironmentValues): void => {
  try {
    const text = JSON.stringify(values);
    storage()?.setItem(`${RUN_ENVIRONMENT_STORAGE_PREFIX}:${key}`, text);
    storage()?.setItem(`${RUN_ENVIRONMENT_STORAGE_PREFIX}:${LAST_USED}`, text);
  } catch {
    // 저장 실패는 이번 화면의 값에 영향이 없다 — 다음 방문에서 스키마 기본값으로 시작할 뿐이다.
  }
};

const NO_FIELDS: readonly RunEnvironmentField[] = [];

/** 연구 구간 거절이 고치라는 칸(요청 본문의 `environment.start`). */
const START_FIELD: keyof RunEnvironment = "start";

/** 시작 거절을 한 번의 누름으로 고치는 교정(검증 랩 V5-05). */
type RunRejectionFix = { label: string; apply: () => void };

/**
 * 사용자가 고친 실행 설정(전략별). `wire` 는 요청 단위 기록이다(저장값·업그레이드 응답과 같은 모양). 표시
 * 값은 렌더 때 스키마로 파생하므로, 스키마를 읽기 전에 채운 값도 스키마가 오면 그대로 보인다 — 칸 목록이
 * 없다고 빈 값으로 덮지 않는다(#352 C-P2-4). `typed` 는 사용자가 친 표시 문자열이다. 표시 단위가 다른 칸
 * (참여율 %)에서 치는 도중의 "12." 를 요청 단위로 바꿨다 되돌리면 소수점이 사라지므로 친 문자열을 보인다.
 */
type ScopedValues = {
  key: string;
  wire: RunEnvironmentValues;
  typed: RunEnvironmentValues;
};
/**
 * 덜 친 날짜 칸(브라우저 `validity.badInput`)의 이름. `settled` 는 칸을 떠날 때, `typing` 은 칸 안(키를 뗌·값이
 * 바뀜)에서 읽은 것이다. 실행 게이트는 둘 다 본다. 비워 둘 수 있는 OOS 칸의 표시는 `settled` 만 봐서, 빈 칸에
 * 치거나 다 친 날짜를 고쳐 치는 도중에 칸 아래·요약 띠·오류 목록이 서지 않는다(#270 P3-R2, #297 리뷰 P3-1).
 * 실행 설정 날짜 칸은 필수라 빈 값 자체가 오류다. 그래서 둘을 합쳐 읽어 "값을 정하세요." 대신 날짜 문장을
 * 보인다(#297 재리뷰 P2-1).
 */
type ScopedDates = {
  key: string;
  settled: ReadonlySet<string>;
  typing: ReadonlySet<string>;
};

const NO_NAMES: ReadonlySet<string> = new Set();
const NO_DATES: ScopedDates = { key: "", settled: NO_NAMES, typing: NO_NAMES };

/** `name` 이 들었는지를 `present` 에 맞춘 집합. 이미 맞으면 같은 집합을 돌려준다. */
const withName = (
  names: ReadonlySet<string>,
  name: string,
  present: boolean,
): ReadonlySet<string> => {
  if (names.has(name) === present) return names;
  const next = new Set(names);
  if (present) next.add(name);
  else next.delete(name);
  return next;
};

/**
 * 패널 열림과 "이 칸으로 가기" 요청. 요약 띠·차단 안내가 패널을 열고 첫 빈 칸에 초점을 옮기는 경로다 —
 * 퀀트에 익숙하지 않은 사용자가 막혔을 때 다음에 할 일이 한 번의 누름이어야 한다(P3-02 리드 보충).
 * `nonce` 는 같은 칸을 다시 요청해도 초점이 다시 가게 한다.
 */
export type RunSettingsPanelState = {
  open: boolean;
  focus: { field: string; nonce: number } | null;
};

/**
 * 실행 설정 패널의 상태 owner(P3-02). `storageKey` 는 전략 id(새 전략은 호출자가 정한 한 칸)다.
 *
 * 실행 설정 칸의 값은 사용자가 고치기 전까지 스키마 기본값과 저장된 마지막 사용값에서 파생하고, 고치면
 * 그 키의 local state 가 된다. 스키마를 아직 못 읽었으면 실행 설정이 없는 것으로 보아 실행을 막는다.
 */
export const useBacktestRunSettings = (storageKey: string) => {
  const schema = useRunEnvironmentSchema();
  const [fields, setFields] = useState<BacktestRunSettingsFields>(
    DEFAULT_BACKTEST_RUN_SETTINGS,
  );
  const [edited, setEdited] = useState<ScopedValues | null>(null);
  // 덜 친 날짜 칸(브라우저 `validity.badInput`). 값은 빈 문자열이라 값 state 로는 표현할 수 없다. 칸 값과
  // 같이 전략별로 묶는다 — 다른 전략으로 옮기면 비운다.
  const [incompleteDates, setIncompleteDates] = useState<ScopedDates | null>(
    null,
  );
  const [panel, setPanel] = useState<RunSettingsPanelState>({
    open: false,
    focus: null,
  });
  const environmentFields = useMemo(
    () =>
      schema.data === undefined
        ? NO_FIELDS
        : runEnvironmentFields(schema.data.schema),
    [schema.data],
  );
  const stored = useMemo(
    () => readStored(storageKey) ?? readStored(LAST_USED),
    [storageKey],
  );
  const scoped = edited !== null && edited.key === storageKey ? edited : null;
  const environmentValues = useMemo(
    () => ({
      ...initialRunEnvironmentValues(
        environmentFields,
        scoped === null ? stored : scoped.wire,
      ),
      ...scoped?.typed,
    }),
    [environmentFields, scoped, stored],
  );
  const dates =
    incompleteDates !== null && incompleteDates.key === storageKey
      ? incompleteDates
      : NO_DATES;
  const environment = useMemo<RunEnvironmentValidation>(
    () =>
      environmentFields.length === 0
        ? { valid: false, environment: null, errors: {} }
        : validateRunEnvironment(
            environmentFields,
            environmentValues,
            new Set([...dates.settled, ...dates.typing]),
          ),
    [dates.settled, dates.typing, environmentFields, environmentValues],
  );
  const result = useMemo(
    () =>
      buildBacktestRunOptions(
        fields,
        environment,
        dates.settled.has(OOS_START_FIELD),
      ),
    [dates.settled, environment, fields],
  );
  const setField = useCallback(
    <Key extends keyof BacktestRunSettingsFields>(
      field: Key,
      value: BacktestRunSettingsFields[Key],
    ): void => setFields((current) => ({ ...current, [field]: value })),
    [],
  );
  const edit = useCallback(
    (wire: RunEnvironmentValues, typed: RunEnvironmentValues): void => {
      setEdited({ key: storageKey, wire, typed });
      // 저장은 요청 단위로 한다 — 표시 단위(참여율 %)가 바뀌어도 저장값의 뜻은 그대로다.
      writeStored(storageKey, wire);
    },
    [storageKey],
  );
  const setEnvironmentValue = useCallback(
    (name: string, value: string): void => {
      const field = environmentFields.find(
        (candidate) => candidate.name === name,
      );
      if (field === undefined) return;
      const base = scoped ?? { wire: stored ?? {}, typed: {} };
      edit(
        { ...base.wire, [name]: runEnvironmentWireText(field, value) },
        { ...base.typed, [name]: value },
      );
    },
    [edit, environmentFields, scoped, stored],
  );
  /**
   * 날짜 칸(실행 설정 칸과 OOS 시작일)이 덜 쳐졌는지 칸이 알려 준다. 칸 안에서 난 일(`leaving` 거짓)은
   * `typing` 에만 넣고 `settled` 에서는 지우기만 한다 — 날짜 선택기로 고르거나 끝까지 친 날짜는 오류를 바로
   * 지운다. `settled` 에 넣는 것은 칸을 떠날 때뿐이다. 바뀐 것이 없으면 state 를 건드리지 않는다.
   */
  const setDateIncomplete = useCallback(
    (name: string, incomplete: boolean, leaving: boolean): void =>
      setIncompleteDates((current) => {
        const scoped =
          current !== null && current.key === storageKey ? current : NO_DATES;
        const settled =
          leaving || !incomplete
            ? withName(scoped.settled, name, incomplete)
            : scoped.settled;
        const nextTyping = withName(
          scoped.typing,
          name,
          !leaving && incomplete,
        );
        return settled === scoped.settled && nextTyping === scoped.typing
          ? current
          : { key: storageKey, settled, typing: nextTyping };
      }),
    [storageKey],
  );
  const setPanelOpen = useCallback(
    (open: boolean): void =>
      setPanel((current) =>
        current.open === open ? current : { ...current, open },
      ),
    [],
  );
  // 실행을 막는 칸(패널 순서). 차단 문장과 "이 칸으로 가기"가 같은 목록을 읽는다(DEFECT-242-01).
  const problems = useMemo(
    () => runSettingsProblems(environmentFields, environment.errors, result),
    [environment.errors, environmentFields, result],
  );
  /** 패널을 열고 아직 맞지 않은 첫 칸(없으면 첫 칸)에 초점을 옮긴다. */
  const openPanelAtFirstProblem = useCallback((): void => {
    const target = problems[0]?.target ?? environmentFields[0]?.name;
    setPanel((current) => ({
      open: true,
      focus:
        target === undefined
          ? current.focus
          : { field: target, nonce: (current.focus?.nonce ?? 0) + 1 },
    }));
  }, [environmentFields, problems]);
  const blockedReason = result.valid
    ? null
    : schema.isError
      ? t("backtest.settings.environment.schemaError")
      : schema.data === undefined
        ? t("backtest.settings.environment.schemaLoading")
        : (runSettingsBlockedReason(problems) ??
          t("backtest.settings.blocked"));
  const fieldLabel = useCallback(
    (path: string): string | null => runFieldLabel(environmentFields, path),
    [environmentFields],
  );
  /**
   * 시작 거절의 교정. 연구 구간 거절이면 시작일을 거절 detail 이 실은 연구 하한으로 옮긴다 — 날짜 owner 는
   * backend 라 화면에 적지 않는다(spec D1). 고칠 수 있는 거절이 아니면 null.
   */
  const rejectionFix = useCallback(
    (
      code: string | null,
      values: Readonly<Record<string, string>>,
    ): RunRejectionFix | null => {
      const researchStart = values.research_start;
      if (
        code !== "backtest.run.research_window_violation" ||
        researchStart === undefined
      )
        return null;
      return {
        label: t("runEnvironment.fix.researchStart").replace(
          "{research_start}",
          researchStart,
        ),
        apply: () => setEnvironmentValue(START_FIELD, researchStart),
      };
    },
    [setEnvironmentValue],
  );
  /**
   * 업그레이드 응답처럼 완성된 실행 설정으로 칸 전부를 바꾼다(사용자가 누른 뒤에만 부른다). 스키마를 아직
   * 읽지 못했어도 값을 그대로 들고, 스키마가 오면 그 값으로 칸을 채운다(#352 C-P2-4).
   */
  const applyEnvironment = useCallback(
    (next: RunEnvironment): void => edit(runEnvironmentWireRecord(next), {}),
    [edit],
  );

  return {
    fields,
    setField,
    result,
    // 치는 중인 덜 친 날짜는 표시하지 않지만 실행은 막는다 — 칸을 떠나지 않고 누른 단축키가 덜 친 OOS 를
    // 빈 칸처럼 보내지 않게 한다(#266 재리뷰 P3-1).
    requestOptions: dates.typing.size > 0 ? null : result.options,
    schemaStatus: schema.isError
      ? ("error" as const)
      : schema.data === undefined
        ? ("loading" as const)
        : ("ready" as const),
    schemaError: schema.error,
    environmentFields,
    environmentValues,
    setEnvironmentValue,
    setDateIncomplete,
    applyEnvironment,
    /** 서버 거절의 `field`(요청 본문 점 경로) → 패널 칸 이름. 모르면 null. */
    runFieldLabel: fieldLabel,
    rejectionFix,
    /** 검증을 통과한 실행 설정. 추적·실행 계획 요청이 같은 값을 싣는다. 없으면 null. */
    environment: environment.valid ? environment.environment : null,
    environmentErrors: environment.errors,
    /**
     * 막힌 칸의 종류. 빈 칸뿐이면 "missing"(요약 띠 버튼이 "실행 설정 채우기"), 값이 틀린 칸이 있으면
     * "invalid"("실행 설정 고치기"). 막힌 칸이 없으면 null.
     */
    problemKind:
      problems.length === 0
        ? null
        : problems.every((problem) => problem.kind === "missing")
          ? ("missing" as const)
          : ("invalid" as const),
    /**
     * 실행을 막는 이유 한 문장. 툴바 차단 안내와 "적용 후 백테스트" 알림이 같은 문장을 쓴다. 실행할 수
     * 있으면 null.
     */
    blockedReason,
    panel,
    setPanelOpen,
    openPanelAtFirstProblem,
  };
};

export type BacktestRunSettingsController = ReturnType<
  typeof useBacktestRunSettings
>;
