import { tName, tOptional } from "../../../shared/config";

/**
 * 실행 설정(`RunEnvironment`) 스키마의 필드 모델(P3-02, spec D6).
 *
 * 필드 목록·순서·enum·기본값·범위·단위·설명 키·카탈로그는 전부 실행 설정 스키마
 * (`GET /api/v1/run-environments/schema`)에서 읽는다. 필드 이름을 여기 손으로 적지 않는다 — 생성 SDK
 * 타입에는 범위가 없어(pydantic 이 `__post_init__` 를 보지 못한다) 그 타입만 믿으면 서버가 거부할 값을
 * 유효하다고 본다. 실행 설정 패널(입력)과 run 상세(기록 표시)가 같은 모델을 읽는다.
 *
 * 숫자 칸의 기본값·범위는 **표시 단위**로 들고 있다. 스키마가 `x-unit: ratio`·`x-display-unit: "%"` 를 주면
 * 화면은 백분율로 보이고 요청은 비율로 보낸다(`scale` = 100). 그 밖의 단위(bp 등)는 그대로다.
 */

export type RunEnvironmentControl = "select" | "number" | "date" | "text";

export type RunEnvironmentField = {
  name: string;
  control: RunEnvironmentControl;
  /** enum 값(선택지). enum 이 아니면 빈 배열. */
  options: readonly string[];
  /** 스키마 `default` 의 표시 단위 문자열. 없으면 null — 기간·유니버스는 기본값이 없다. */
  defaultValue: string | null;
  required: boolean;
  /** 표시 단위의 범위. */
  minimum: number | null;
  exclusiveMinimum: number | null;
  maximum: number | null;
  exclusiveMaximum: number | null;
  /** `x-display-unit`(예: `%`·`bp`). 없으면 null. */
  displayUnit: string | null;
  /** 표시 값 = 요청 값 × scale. */
  scale: number;
  /** 이름(`<key>`)·한 줄 뜻(`<key>.description`)·enum 값 이름(`<key>.value.<값>`)의 stem. */
  descriptionKey: string | null;
  /** `x-catalog`. 목록 endpoint 가 없는 카탈로그(`universe`)는 텍스트 입력이다. */
  catalog: string | null;
};

type Json = Record<string, unknown>;

const isRecord = (value: unknown): value is Json =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const numberOrNull = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const stringOrNull = (value: unknown): string | null =>
  typeof value === "string" ? value : null;

/** 스키마 칸의 JSON 타입. 선택 칸(`anyOf: [{type}, {type: "null"}]`)은 null 아닌 쪽 타입이다. */
const typeOf = (node: Json): unknown =>
  Array.isArray(node.anyOf)
    ? node.anyOf.find((member) => isRecord(member) && member.type !== "null")
        ?.type
    : node.type;

const controlOf = (node: Json): RunEnvironmentControl => {
  if (Array.isArray(node.enum)) return "select";
  const type = typeOf(node);
  if (type === "number" || type === "integer") return "number";
  if (node.format === "date") return "date";
  return "text";
};

/**
 * 부동소수 곱셈 꼬리(0.1 × 100 = 10.000000000000002)를 지운 숫자. 유효숫자 12자리로 자르므로 그보다 긴
 * 값은 반올림된다(lang2 PLAN P3-02 결정 2, #251).
 */
const tidy = (value: number): number => Number(value.toPrecision(12));

const scaleOf = (node: Json): number =>
  node["x-unit"] === "ratio" && node["x-display-unit"] === "%" ? 100 : 1;

const scaled = (value: number | null, scale: number): number | null =>
  value === null ? null : tidy(value * scale);

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
    const scale = scaleOf(node);
    const control = controlOf(node);
    const fallback = node.default;
    return [
      {
        name,
        control,
        options: Array.isArray(node.enum) ? node.enum.map(String) : [],
        defaultValue:
          fallback === undefined || fallback === null
            ? null
            : control === "number" && typeof fallback === "number"
              ? String(tidy(fallback * scale))
              : String(fallback),
        required: required.has(name),
        minimum: scaled(numberOrNull(node.minimum), scale),
        exclusiveMinimum: scaled(numberOrNull(node.exclusiveMinimum), scale),
        maximum: scaled(numberOrNull(node.maximum), scale),
        exclusiveMaximum: scaled(numberOrNull(node.exclusiveMaximum), scale),
        displayUnit: stringOrNull(node["x-display-unit"]),
        scale,
        descriptionKey: stringOrNull(node["x-description-key"]),
        catalog: stringOrNull(node["x-catalog"]),
      },
    ];
  });
};

/** 요청(기록) 값 → 입력 칸의 표시 문자열. 숫자가 아니면 문자열 그대로. */
export const runEnvironmentDisplayValue = (
  field: RunEnvironmentField,
  value: unknown,
): string => {
  if (value === undefined || value === null) return "";
  if (field.control !== "number" || field.scale === 1) return String(value);
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) && String(value).trim() !== ""
    ? String(tidy(number * field.scale))
    : String(value);
};

/** 입력 칸의 표시 문자열 → 요청 값의 문자열. 숫자가 아니면 그대로 둔다(검증이 막는다). */
export const runEnvironmentWireText = (
  field: RunEnvironmentField,
  text: string,
): string => {
  if (field.control !== "number" || field.scale === 1) return text;
  const number = Number(text);
  return text.trim() !== "" && Number.isFinite(number)
    ? String(tidy(number / field.scale))
    : text;
};

/** 표시 단위 숫자 → 요청 값. */
export const runEnvironmentWireNumber = (
  field: RunEnvironmentField,
  value: number,
): number => (field.scale === 1 ? value : tidy(value / field.scale));

/** 칸 이름. 설명 키가 없으면 필드 이름을 그대로 보인다(스키마가 키를 발행하지 않은 경우). */
export const runEnvironmentName = (field: RunEnvironmentField): string =>
  tName(field.descriptionKey) ?? field.name;

/** 입력 칸 라벨: 이름 뒤에 스키마의 표시 단위를 붙인다(예: "참여율 (%)"). */
export const runEnvironmentLabel = (field: RunEnvironmentField): string =>
  field.displayUnit === null
    ? runEnvironmentName(field)
    : `${runEnvironmentName(field)} (${field.displayUnit})`;

/** enum 값의 이름(`<key>.value.<값>`). 번역이 없으면 값 그대로. */
export const runEnvironmentOptionLabel = (
  field: RunEnvironmentField,
  value: string,
): string =>
  (field.descriptionKey === null
    ? undefined
    : tOptional(`${field.descriptionKey}.value.${value}`)) ?? value;

/** 기록된 값 하나를 읽는 문장으로: enum 은 값 이름, 숫자는 표시 단위를 붙인다. */
export const runEnvironmentValueLabel = (
  field: RunEnvironmentField,
  value: unknown,
): string => {
  if (value === undefined || value === null || value === "") return "—";
  if (field.control === "select")
    return runEnvironmentOptionLabel(field, String(value));
  const text = runEnvironmentDisplayValue(field, value);
  return field.control === "number" && field.displayUnit !== null
    ? `${text}${field.displayUnit}`
    : text;
};
