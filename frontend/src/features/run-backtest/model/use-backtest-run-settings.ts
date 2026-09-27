import { useCallback, useMemo, useState } from "react";

import { useRunEnvironmentSchema } from "../../../entities/backtest";
import type { RunEnvironment } from "../../../shared/api";
import {
  initialRunEnvironmentValues,
  runEnvironmentFields,
  runEnvironmentValuesOf,
  validateRunEnvironment,
  type RunEnvironmentField,
  type RunEnvironmentValidation,
  type RunEnvironmentValues,
} from "./run-environment";
import {
  buildBacktestRunOptions,
  DEFAULT_BACKTEST_RUN_SETTINGS,
  type BacktestRunSettingsFields,
} from "./run-settings";

/**
 * 실행 설정의 마지막 사용값(spec D6: 전략별 local UI state). 서버는 run manifest 에만 기록한다.
 * 전략별 칸이 없으면 마지막으로 쓴 값을 쓴다 — 새 전략을 저장해 전략 id 가 생겨도 방금 고른 기간·
 * 유니버스가 이어진다. 저장소를 쓸 수 없으면(사생활 모드 등) 조용히 스키마 기본값으로 시작한다.
 */
export const RUN_ENVIRONMENT_STORAGE_PREFIX =
  "quant-workbench.run-environment.v1";
const LAST_USED = "last";

const storage = (): Storage | null => {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
};

const readStored = (key: string): RunEnvironmentValues | null => {
  try {
    const text = storage()?.getItem(`${RUN_ENVIRONMENT_STORAGE_PREFIX}:${key}`);
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

type ScopedValues = { key: string; values: RunEnvironmentValues };

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
  const environmentValues = useMemo(
    () =>
      edited !== null && edited.key === storageKey
        ? edited.values
        : initialRunEnvironmentValues(environmentFields, stored),
    [edited, environmentFields, storageKey, stored],
  );
  const environment = useMemo<RunEnvironmentValidation>(
    () =>
      environmentFields.length === 0
        ? { valid: false, environment: null, errors: {} }
        : validateRunEnvironment(environmentFields, environmentValues),
    [environmentFields, environmentValues],
  );
  const result = useMemo(
    () => buildBacktestRunOptions(fields, environment),
    [environment, fields],
  );
  const setField = useCallback(
    <Key extends keyof BacktestRunSettingsFields>(
      field: Key,
      value: BacktestRunSettingsFields[Key],
    ): void => setFields((current) => ({ ...current, [field]: value })),
    [],
  );
  const replaceEnvironment = useCallback(
    (next: RunEnvironmentValues): void => {
      setEdited({ key: storageKey, values: next });
      writeStored(storageKey, next);
    },
    [storageKey],
  );
  const setEnvironmentValue = useCallback(
    (name: string, value: string): void =>
      replaceEnvironment({ ...environmentValues, [name]: value }),
    [environmentValues, replaceEnvironment],
  );
  /** 업그레이드 응답처럼 완성된 실행 설정으로 칸 전부를 바꾼다(사용자가 누른 뒤에만 부른다). */
  const applyEnvironment = useCallback(
    (next: RunEnvironment): void =>
      replaceEnvironment(runEnvironmentValuesOf(environmentFields, next)),
    [environmentFields, replaceEnvironment],
  );

  return {
    fields,
    setField,
    result,
    requestOptions: result.options,
    schemaStatus: schema.isError
      ? ("error" as const)
      : schema.data === undefined
        ? ("loading" as const)
        : ("ready" as const),
    schemaError: schema.error,
    environmentFields,
    environmentValues,
    setEnvironmentValue,
    applyEnvironment,
    /** 검증을 통과한 실행 설정. 추적·실행 계획 요청이 같은 값을 싣는다. 없으면 null. */
    environment: environment.valid ? environment.environment : null,
    environmentErrors: environment.errors,
  };
};

export type BacktestRunSettingsController = ReturnType<
  typeof useBacktestRunSettings
>;
