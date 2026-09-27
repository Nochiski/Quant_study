import type { RunEnvironment } from "../../../shared/api";

/**
 * 실행 설정(`RunEnvironment`) 패널의 필드 모델(P3-02, spec D6).
 *
 * 필드 목록·순서·enum·기본값·범위·설명 키·카탈로그는 전부 실행 설정 스키마
 * (`GET /api/v1/run-environments/schema`)에서 읽는다. 필드 이름을 여기 손으로 적지 않는다 — 생성 SDK
 * 타입에는 범위가 없어(pydantic 이 `__post_init__` 를 보지 못한다) 그 타입만 믿으면 서버가 거부할 값을
 * 유효하다고 본다. 이 모듈의 검증은 빠른 피드백이고 최종 판정은 backend 가 한다.
 */

export type RunEnvironmentControl = "select" | "number" | "date" | "text";

export type RunEnvironmentField = {
  name: string;
  control: RunEnvironmentControl;
  /** enum 값(선택지). enum 이 아니면 빈 배열. */
  options: readonly string[];
  /** 스키마 `default` 의 문자열 표기. 없으면 null — 기간·유니버스는 기본값이 없다. */
  defaultValue: string | null;
  required: boolean;
  minimum: number | null;
  exclusiveMinimum: number | null;
  maximum: number | null;
  exclusiveMaximum: number | null;
  /** 이름(`<key>`)·한 줄 뜻(`<key>.description`)·enum 값 이름(`<key>.value.<값>`)의 stem. */
  descriptionKey: string | null;
  /** `x-catalog`. 목록 endpoint 가 없는 카탈로그(`universe`)는 텍스트 입력이다. */
  catalog: string | null;
};

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
  | "order";

export type RunEnvironmentValidation =
  | { valid: true; environment: RunEnvironment; errors: Record<string, never> }
  | {
      valid: false;
      environment: null;
      errors: Readonly<Record<string, RunEnvironmentFieldError>>;
    };

type Json = Record<string, unknown>;

const isRecord = (value: unknown): value is Json =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const numberOrNull = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const stringOrNull = (value: unknown): string | null =>
  typeof value === "string" ? value : null;

const controlOf = (node: Json): RunEnvironmentControl => {
  if (Array.isArray(node.enum)) return "select";
  if (node.type === "number" || node.type === "integer") return "number";
  if (node.format === "date") return "date";
  return "text";
};

/** 스키마 `properties` 순서 그대로의 필드 목록. */
export const runEnvironmentFields = (schema: Json): RunEnvironmentField[] => {
  const properties = isRecord(schema.properties) ? schema.properties : {};
  const required = new Set(
    Array.isArray(schema.required)
      ? schema.required.filter(
          (name): name is string => typeof name === "string",
        )
      : [],
  );
  return Object.entries(properties).flatMap(([name, node]) => {
    if (!isRecord(node)) return [];
    const fallback = node.default;
    return [
      {
        name,
        control: controlOf(node),
        options: Array.isArray(node.enum) ? node.enum.map(String) : [],
        defaultValue:
          fallback === undefined || fallback === null ? null : String(fallback),
        required: required.has(name),
        minimum: numberOrNull(node.minimum),
        exclusiveMinimum: numberOrNull(node.exclusiveMinimum),
        maximum: numberOrNull(node.maximum),
        exclusiveMaximum: numberOrNull(node.exclusiveMaximum),
        descriptionKey: stringOrNull(node["x-description-key"]),
        catalog: stringOrNull(node["x-catalog"]),
      },
    ];
  });
};

/**
 * 패널의 첫 값. 저장된 마지막 사용값이 있으면 그 필드는 그 값, 없으면 스키마 기본값, 기본값도 없으면 빈 칸.
 * 저장값은 스키마에 있는 필드만 받는다(스키마가 바뀐 뒤 남은 옛 키는 버린다).
 */
export const initialRunEnvironmentValues = (
  fields: readonly RunEnvironmentField[],
  stored: RunEnvironmentValues | null,
): RunEnvironmentValues =>
  Object.fromEntries(
    fields.map((field) => [
      field.name,
      stored?.[field.name] ?? field.defaultValue ?? "",
    ]),
  );

/**
 * 업그레이드 응답처럼 완성된 `RunEnvironment` 를 패널 값으로 옮긴다. 스키마에 없는 키는 버린다.
 */
export const runEnvironmentValuesOf = (
  fields: readonly RunEnvironmentField[],
  environment: RunEnvironment,
): RunEnvironmentValues => {
  const record = environment as unknown as Json;
  return Object.fromEntries(
    fields.map((field) => {
      const value = record[field.name];
      return [
        field.name,
        value === undefined || value === null ? "" : String(value),
      ];
    }),
  );
};

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
 * 칸마다 스키마 규칙(필수·숫자·범위·날짜·enum)을 보고, 전부 맞으면 요청에 실을 `RunEnvironment` 를 만든다.
 *
 * 기간 순서(`start <= end`)는 스키마가 말하지 않는 `RunEnvironment.__post_init__` 규칙이다. 생성 타입의
 * 두 필드 이름으로 빠른 피드백만 하고, 어긋나면 서버가 다시 거절한다.
 */
export const validateRunEnvironment = (
  fields: readonly RunEnvironmentField[],
  values: RunEnvironmentValues,
): RunEnvironmentValidation => {
  const errors: Record<string, RunEnvironmentFieldError> = {};
  const environment: Record<string, string | number> = {};
  for (const field of fields) {
    const text = (values[field.name] ?? "").trim();
    if (text === "") {
      if (field.required || field.defaultValue !== null)
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
      environment[field.name] = value;
      continue;
    }
    if (field.control === "date" && !validDate(text)) {
      errors[field.name] = "date";
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
