import {
  runEnvironmentDisplayValue,
  runEnvironmentWireNumber,
  runEnvironmentWireText,
  type RunEnvironmentField,
} from "../../../entities/backtest";
import { projectApplicability, type RunEnvironment } from "../../../shared/api";

/**
 * 실행 설정(`RunEnvironment`) 패널의 입력 값과 검증(P3-02, spec D6).
 *
 * 필드 모델(목록·순서·enum·기본값·범위·단위)은 `entities/backtest` 의 `runEnvironmentFields` 가 스키마에서
 * 읽는다. 입력 칸 값은 **표시 단위** 문자열이고(참여율은 %), 요청에 실을 때와 저장할 때만 요청 단위로
 * 바꾼다. 이 모듈의 검증은 빠른 피드백이고 최종 판정은 backend 가 한다.
 */

export {
  runEnvironmentFields,
  type RunEnvironmentControl,
  type RunEnvironmentField,
} from "../../../entities/backtest";

/** 입력 칸의 문자열 값. 필드 이름 → 값. */
export type RunEnvironmentValues = Readonly<Record<string, string>>;

export type RunEnvironmentFieldError =
  | "required"
  | "number"
  | "minimum"
  | "exclusiveMinimum"
  | "maximum"
  | "exclusiveMaximum"
  | "date"
  | "dateRange"
  | "order";

export type RunEnvironmentValidation =
  | { valid: true; environment: RunEnvironment; errors: Record<string, never> }
  | {
      valid: false;
      environment: null;
      errors: Readonly<Record<string, RunEnvironmentFieldError>>;
    };

/**
 * 패널의 첫 값. 저장된 마지막 사용값이 있으면 그 필드는 그 값, 없으면 스키마 기본값, 기본값도 없으면 빈 칸.
 * 저장값은 요청 단위 문자열이라 표시 단위로 바꿔 받고, 스키마에 있는 필드만 받는다(스키마가 바뀐 뒤 남은
 * 옛 키는 버린다).
 */
export const initialRunEnvironmentValues = (
  fields: readonly RunEnvironmentField[],
  stored: RunEnvironmentValues | null,
): RunEnvironmentValues =>
  Object.fromEntries(
    fields.map((field) => {
      const saved = stored?.[field.name];
      return [
        field.name,
        saved === undefined
          ? (field.defaultValue ?? "")
          : runEnvironmentDisplayValue(field, saved),
      ];
    }),
  );

/**
 * 저장할 칸(요청 단위): 스키마 기본값과 다른 칸만 남긴다. 기본값이 바뀌면 사용자가 손대지 않은 칸은 새
 * 기본값을 따른다(#396 리뷰 P2-1). 칸 목록을 아직 모르면(스키마를 읽기 전) 가리지 않는다.
 */
export const runEnvironmentStoredValues = (
  fields: readonly RunEnvironmentField[],
  wire: RunEnvironmentValues,
): RunEnvironmentValues =>
  Object.fromEntries(
    Object.entries(wire).filter(([name, value]) => {
      const field = fields.find((candidate) => candidate.name === name);
      return (
        field?.defaultValue === null ||
        field?.defaultValue === undefined ||
        value !== runEnvironmentWireText(field, field.defaultValue)
      );
    }),
  );

/**
 * 업그레이드 응답처럼 완성된 `RunEnvironment` 를 요청 단위 기록(저장값과 같은 모양)으로 옮긴다. 값이 없는
 * (null) 칸은 뺀다. 스키마가 필요 없어 스키마를 읽기 전에도 값을 잃지 않는다(#352 C-P2-4).
 */
export const runEnvironmentWireRecord = (
  environment: RunEnvironment,
): RunEnvironmentValues =>
  Object.fromEntries(
    Object.entries(environment).flatMap(([name, value]: [string, unknown]) =>
      value === null || value === undefined ? [] : [[name, String(value)]],
    ),
  );

/**
 * 날짜 칸이 받는 범위(#264). 범위를 주지 않으면 Chromium 이 `<input type="date">` 의 연도를 6자리(275760년)까지
 * 받아, 날짜를 숫자로 이어 치면 월·일이 연도로 빨려 들어가고 칸이 빈다. 최댓값의 연도가 4자리면 4자리 뒤에 월로
 * 넘어간다. 실행 설정 스키마에는 날짜 상·하한 어휘가 없어(JSON Schema 2020-12 에 `format: date` 범위 키워드가
 * 없다) 여기서 정한다. 하한은 데이터가 없는 먼 과거의 오타(0021년 등)를 칸에서 잡으려는 값이다.
 */
export const DATE_INPUT_MINIMUM = "1900-01-01";
export const DATE_INPUT_MAXIMUM = "9999-12-31";

/**
 * 칸이 지금 값에서 읽히는가(스키마 `x-applicable-when`, #352). 조건이 없는 칸은 늘 읽힌다. 읽히지 않는 칸은
 * 패널이 끄고 검사하지도 요청에 싣지도 않는다 — 조건 칸 값이 없어 판정할 수 없으면 읽히는 쪽으로 둔다.
 */
export const runEnvironmentApplies = (
  field: RunEnvironmentField,
  values: RunEnvironmentValues,
): boolean =>
  field.applicableWhen === null ||
  projectApplicability(field.applicableWhen, values).applicable !== false;

/** 덜 친 날짜 칸이 없다. */
const NO_INCOMPLETE: ReadonlySet<string> = new Set();

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

const validDate = (value: string): boolean => {
  if (!ISO_DATE.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00.000Z`);
  return (
    !Number.isNaN(parsed.valueOf()) &&
    parsed.toISOString().slice(0, 10) === value
  );
};

const numberError = (
  field: RunEnvironmentField,
  value: number,
): RunEnvironmentFieldError | null => {
  if (field.minimum !== null && value < field.minimum) return "minimum";
  if (field.exclusiveMinimum !== null && value <= field.exclusiveMinimum)
    return "exclusiveMinimum";
  if (field.maximum !== null && value > field.maximum) return "maximum";
  if (field.exclusiveMaximum !== null && value >= field.exclusiveMaximum)
    return "exclusiveMaximum";
  return null;
};

/**
 * `incomplete` 는 브라우저가 덜 친 날짜라고 알려 준 칸 이름이다(`validity.badInput`, 값은 빈 문자열).
 *
 * 칸마다 스키마 규칙(필수·숫자·범위·날짜·enum·적용 조건)을 보고, 전부 맞으면 요청에 실을 `RunEnvironment` 를
 * 만든다. 적용 조건이 서지 않는 칸은 건너뛰고, 서는 칸은 비울 수 없다(직접 입력 세율은 `custom` 에서만 필수).
 *
 * 기간 순서(`start <= end`)는 스키마가 말하지 않는 `RunEnvironment.__post_init__` 규칙이다. 생성 타입의
 * 두 필드 이름으로 빠른 피드백만 하고, 어긋나면 서버가 다시 거절한다.
 */
export const validateRunEnvironment = (
  fields: readonly RunEnvironmentField[],
  values: RunEnvironmentValues,
  incomplete: ReadonlySet<string> = NO_INCOMPLETE,
): RunEnvironmentValidation => {
  const errors: Record<string, RunEnvironmentFieldError> = {};
  const environment: Record<string, string | number> = {};
  for (const field of fields) {
    if (!runEnvironmentApplies(field, values)) continue;
    const text = (values[field.name] ?? "").trim();
    if (text === "") {
      // 덜 친 날짜 칸도 값은 빈 문자열이다. 칸이 알려 준 덜 친 상태면 "비었다"가 아니라 날짜 오류로 본다 —
      // 칸 아래 문장과 요약 띠·차단 문장이 같은 원인을 말하게 한다(#266 리뷰 P3-1).
      if (field.control === "date" && incomplete.has(field.name))
        errors[field.name] = "date";
      else if (
        field.required ||
        field.defaultValue !== null ||
        field.applicableWhen !== null
      )
        errors[field.name] = "required";
      continue;
    }
    if (field.control === "number") {
      const value = Number(text);
      if (!Number.isFinite(value)) {
        errors[field.name] = "number";
        continue;
      }
      const bound = numberError(field, value);
      if (bound !== null) {
        errors[field.name] = bound;
        continue;
      }
      environment[field.name] = runEnvironmentWireNumber(field, value);
      continue;
    }
    if (field.control === "date" && !validDate(text)) {
      errors[field.name] = "date";
      continue;
    }
    if (
      field.control === "date" &&
      (text < DATE_INPUT_MINIMUM || text > DATE_INPUT_MAXIMUM)
    ) {
      errors[field.name] = "dateRange";
      continue;
    }
    if (field.control === "select" && !field.options.includes(text)) {
      errors[field.name] = "required";
      continue;
    }
    environment[field.name] = text;
  }
  const typed = environment as unknown as Partial<RunEnvironment>;
  if (
    typeof typed.start === "string" &&
    typeof typed.end === "string" &&
    errors.end === undefined &&
    typed.start > typed.end
  )
    errors.end = "order";
  if (Object.keys(errors).length > 0)
    return { valid: false, environment: null, errors };
  return {
    valid: true,
    environment: environment as unknown as RunEnvironment,
    errors: {},
  };
};
